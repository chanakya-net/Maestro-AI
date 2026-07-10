#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTER_PATH="${ROOT_DIR}/assets/run-with-it-router.py"
REGISTRY_PATH="${ROOT_DIR}/assets/agent-registry.json"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_json_field() {
  local json="$1"
  local expression="$2"
  local message="$3"
  python3 - "$json" "$expression" "$message" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
expression = sys.argv[2]
message = sys.argv[3]

if not eval(expression, {"payload": payload}):
    raise SystemExit(f"FAIL: {message} ({expression})")
PY
}

assert_file_exists() {
  local file="$1"
  local message="$2"
  [[ -f "${file}" ]] || fail "${message} (missing: ${file})"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "${haystack}" == *"${needle}"* ]] || fail "${message} (missing: ${needle})"
}

assert_file_exists "${ROUTER_PATH}" "router helper exists"
[[ -x "${ROUTER_PATH}" ]] || fail "router helper is executable"

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

PYTHONDONTWRITEBYTECODE=1 python3 - "${REGISTRY_PATH}" "${ROUTER_PATH}" <<'PY'
import importlib.util
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    registry = json.load(handle)

distribution = registry["model_routing"]["usage_distribution"]
target = distribution["default_target_percent"]
expected = {
    "codex": 60,
    "claude": 35,
    "agy": 5,
}
if target != expected:
    raise SystemExit(f"default target mismatch: {target!r}")
if sum(target.values()) != 100:
    raise SystemExit("default target must sum to 100")
for role in ("complexity", "impl", "review", "modify", "artifact-recovery", "merge-recovery"):
    if role not in distribution["role_target_percent"]:
        raise SystemExit(f"missing role target for {role}")
if distribution["role_target_percent"]["complexity"] != {"agy": 50, "codex": 25, "claude": 25}:
    raise SystemExit(f"complexity target mismatch: {distribution['role_target_percent']['complexity']!r}")
if distribution["role_band_target_percent"]["complexity"]["quite-easy"] != {"codex": 40, "claude": 35, "agy": 25}:
    raise SystemExit(f"quite-easy complexity target mismatch: {distribution['role_band_target_percent']['complexity']['quite-easy']!r}")

copilot = registry["agents"]["github-copilot"]
if copilot.get("routing_disabled") is not True:
    raise SystemExit("GitHub Copilot must be permanently disabled for routing")
if "exhausted" not in copilot.get("routing_disabled_reason", "").lower():
    raise SystemExit("GitHub Copilot disabled reason must mention exhausted plan")

def assert_no_positive_copilot_targets(node, path="usage_distribution"):
    if isinstance(node, dict):
        for key, value in node.items():
            next_path = f"{path}.{key}"
            if key == "github-copilot" and isinstance(value, (int, float)) and value > 0:
                raise SystemExit(f"GitHub Copilot has positive routing target at {next_path}: {value}")
            assert_no_positive_copilot_targets(value, next_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            assert_no_positive_copilot_targets(value, f"{path}[{index}]")

for key in ("default_target_percent", "role_target_percent", "role_band_target_percent"):
    assert_no_positive_copilot_targets(distribution.get(key, {}), key)

for role, preference in distribution.get("role_agent_preference", {}).items():
    if "github-copilot" in preference:
        raise SystemExit(f"GitHub Copilot remains in {role} role preference")

codex_model = registry["agents"]["codex"]["model"]
expected_codex_models = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
]
if codex_model.get("known_models") != expected_codex_models:
    raise SystemExit(f"codex known models mismatch: {codex_model.get('known_models')!r}")
if "gpt-5.3-codex-spark" in codex_model.get("routing_disabled_models", []):
    raise SystemExit("codex registry must not disable Spark after the weekly limit reset")

claude_model = registry["agents"]["claude"]["model"]
if claude_model.get("known_models") != ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]:
    raise SystemExit(f"claude known models mismatch: {claude_model.get('known_models')!r}")
catalog = registry["model_catalog"]
expected_gpt56 = {
    "gpt-5.6-luna": ("balanced", 3, "easy"),
    "gpt-5.6-terra": ("advanced", 5, "medium"),
    "gpt-5.6-sol": ("frontier", 7, "medium-hard"),
}
for model_id, (ability, weight, min_band) in expected_gpt56.items():
    entry = catalog[model_id]
    assert entry["ability"] == ability
    assert entry["complexity_weight"] == weight
    assert entry["min_band"] == min_band
    assert entry["context_window"] == 372000
    assert entry["reasoning_effort"] == "high"

assert catalog["gpt-5.5"]["explicit_only"] is True
assert "gpt-5.5" not in registry["model_routing"]["band_required_models"]["complex"]
assert "gpt-5.5" not in registry["model_routing"]["band_required_models"]["holy-fuck"]
assert "gpt-5.6-sol" in registry["model_routing"]["band_required_models"]["holy-fuck"]
if "claude-opus-4.7" in catalog:
    raise SystemExit("Opus 4.7 must be removed from the model catalog; use only Opus 4.8 series")
if "claude-opus-4-8" not in catalog:
    raise SystemExit("Opus 4.8 must remain in the model catalog")
if catalog["gpt-5.3-codex-spark"].get("routing_disabled") is True:
    raise SystemExit("Codex Spark must be routable after the weekly limit reset")

spec = importlib.util.spec_from_file_location("run_with_it_router", sys.argv[2])
router = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(router)

def automatic(level, role="impl"):
    return router.candidate_model_ids(registry, role, level, None, None)

assert "gpt-5.6-luna" not in automatic("quite-easy")
assert "gpt-5.6-luna" in automatic("easy")
assert "gpt-5.6-luna" not in automatic("medium")
assert "gpt-5.6-terra" in automatic("medium")
assert "gpt-5.6-terra" not in automatic("medium-hard")
assert "gpt-5.6-sol" in automatic("medium-hard")
assert "gpt-5.6-sol" in automatic("complex")
assert "gpt-5.6-sol" in automatic("holy-fuck")
for level in ("quite-easy", "easy", "medium", "medium-hard", "complex", "holy-fuck"):
    assert "gpt-5.5" not in automatic(level)

automatic_bands = {
    "gpt-5.6-luna": ["easy"],
    "gpt-5.6-terra": ["medium"],
    "gpt-5.6-sol": ["medium-hard", "complex", "holy-fuck"],
}
for model_id, expected_bands in automatic_bands.items():
    assert catalog[model_id].get("routing_bands") == expected_bands
    for role in ("impl", "complexity"):
        for level in ("quite-easy", "easy", "medium", "medium-hard", "complex", "holy-fuck"):
            assert (model_id in automatic(level, role)) == (level in expected_bands)

assert router.candidate_model_ids(registry, "impl", "complex", "gpt-5.5", None) == ["gpt-5.5"]
assert router.candidate_model_ids(registry, "complexity", "complex", "gpt-5.6-luna", None) == ["gpt-5.6-luna"]
PY

echo "PASS: registry declares subscription usage distribution"

UNBLOCKED_COPILOT_REGISTRY="${WORK_DIR}/unblocked-copilot-registry.json"
python3 - "${REGISTRY_PATH}" "${UNBLOCKED_COPILOT_REGISTRY}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    registry = json.load(handle)

registry["agents"]["github-copilot"].pop("routing_disabled", None)
registry["agents"]["github-copilot"].pop("routing_disabled_reason", None)

with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(registry, handle, indent=2)
    handle.write("\n")
PY

set +e
hard_block_error="$("${ROUTER_PATH}" \
  --registry-file "${UNBLOCKED_COPILOT_REGISTRY}" \
  --ledger-file "${WORK_DIR}/hard-block-ledger.json" \
  --role impl \
  --complexity-level easy \
  --detected-agents github-copilot 2>&1 >/dev/null)"
hard_block_status=$?
set -e

[[ "${hard_block_status}" -ne 0 ]] || fail "router must hard-block Copilot even if a registry copy omits routing_disabled"
assert_contains "${hard_block_error}" "github-copilot" "router hard-block diagnostics mention Copilot"

echo "PASS: router hard-blocks Copilot beyond registry metadata"

cat > "${WORK_DIR}/codex-heavy-ledger.json" <<'JSON'
{
  "schema_version": 1,
  "totals": {
    "agents": {
      "codex": 9,
      "agy": 1,
      "github-copilot": 1,
      "claude": 1
    }
  },
  "decisions": []
}
JSON

set +e
disabled_only_error="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/disabled-only-ledger.json" \
  --role impl \
  --complexity-level easy \
  --detected-agents github-copilot 2>&1 >/dev/null)"
disabled_only_status=$?
set -e

[[ "${disabled_only_status}" -ne 0 ]] || fail "router must reject Copilot as the only detected agent"
assert_contains "${disabled_only_error}" "no compatible routing candidates" "router explains disabled-only Copilot has no candidates"
assert_contains "${disabled_only_error}" "github-copilot" "router diagnostics mention disabled Copilot"

echo "PASS: router rejects disabled Copilot even when detected"

codex_heavy_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/codex-heavy-ledger.json" \
  --role impl \
  --complexity-level easy \
  --detected-agents codex,agy,github-copilot,claude)"

assert_json_field "${codex_heavy_output}" 'payload["agent"] in {"claude", "agy"}' "codex-heavy ledger shifts easy implementation away from Codex without using Copilot"
assert_json_field "${codex_heavy_output}" 'payload["policy"]["default_target_percent"]["codex"] == 60' "router reports Codex 60 percent default target"
assert_json_field "${codex_heavy_output}" 'payload["policy"]["default_target_percent"]["claude"] == 35' "router reports Claude 35 percent default target"
assert_json_field "${codex_heavy_output}" 'payload["ledger"]["updated"] is False' "router does not update ledger unless requested"

echo "PASS: router shifts easy work away from over-target Codex"

complexity_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/empty-ledger.json" \
  --role complexity \
  --complexity-level medium \
  --detected-agents codex,agy,github-copilot,claude)"

assert_json_field "${complexity_output}" 'payload["agent"] == "agy"' "complexity scoring routes medium scoring work to Agy when all tools are available"

echo "PASS: router shifts complexity scoring toward Agy"

spark_forced_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/spark-forced-ledger.json" \
  --role impl \
  --complexity-level medium \
  --detected-agents codex \
  --forced-agent codex \
  --forced-model gpt-5.3-codex-spark)"

assert_json_field "${spark_forced_output}" 'payload["agent"] == "codex"' "forced Spark route uses Codex"
assert_json_field "${spark_forced_output}" 'payload["model"] == "gpt-5.3-codex-spark"' "router allows Codex Spark after weekly limit reset"

echo "PASS: router allows Codex Spark after reset"

artifact_recovery_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/empty-ledger.json" \
  --role artifact-recovery \
  --complexity-level medium-hard \
  --detected-agents codex,agy,github-copilot,claude)"

assert_json_field "${artifact_recovery_output}" 'payload["role"] == "artifact-recovery"' "router accepts artifact recovery role"
assert_json_field "${artifact_recovery_output}" 'payload["agent"] in {"codex", "claude"}' "artifact recovery avoids Agy and disabled Copilot unless stronger tools are unavailable"

echo "PASS: router selects artifact recovery worker"

review_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/review-ledger.json" \
  --role review \
  --complexity-level medium-hard \
  --exclude-model gpt-5.3-codex \
  --detected-agents codex,agy,github-copilot,claude)"

assert_json_field "${review_output}" 'payload["model"] != "gpt-5.3-codex"' "review excludes implementation model"
assert_json_field "${review_output}" 'payload["agent"] in {"codex", "claude"}' "review avoids Agy and disabled Copilot unless higher-priority review tools are unavailable"

echo "PASS: router selects independent review model"

model_denylist_output="$(RUN_WITH_IT_MODEL_DENYLIST="codex/gpt-5.5,codex/gpt-5.3-codex,codex/gpt-5.3-codex-spark" "${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/model-denylist-ledger.json" \
  --role impl \
  --complexity-level complex \
  --detected-agents codex)"

assert_json_field "${model_denylist_output}" 'payload["model"] != "gpt-5.5"' "environment model denylist excludes denied agent/model pair"
assert_json_field "${model_denylist_output}" 'payload["model"] not in {"gpt-5.3-codex", "gpt-5.3-codex-spark"}' "environment model denylist excludes denied Codex 5.3 pairs"

echo "PASS: router honors runtime model denylist"

cat > "${WORK_DIR}/availability-cache.json" <<'JSON'
{
  "unavailable": [
    {
      "agent": "codex",
      "model": "gpt-5.5",
      "reason": "quota"
    }
  ]
}
JSON

availability_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/availability-ledger.json" \
  --role impl \
  --complexity-level complex \
  --detected-agents codex \
  --availability-file "${WORK_DIR}/availability-cache.json")"

assert_json_field "${availability_output}" 'payload["model"] != "gpt-5.5"' "availability cache excludes unavailable agent/model pair"

echo "PASS: router honors model availability cache"

forced_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/forced-ledger.json" \
  --role impl \
  --complexity-level complex \
  --detected-agents codex,agy,github-copilot,claude \
  --forced-agent claude \
  --forced-model claude-sonnet-4-6)"

assert_json_field "${forced_output}" 'payload["agent"] == "claude"' "forced agent wins over distribution"
assert_json_field "${forced_output}" 'payload["model"] == "claude-sonnet-4-6"' "forced model wins over distribution"
assert_json_field "${forced_output}" 'payload["selection_reason"] == "forced-agent-and-model"' "router reports forced selection reason"

echo "PASS: router honors explicit overrides"

# --- sticky reviewer preference (issue 641): keep the same reviewer across cycles ---
prefer_base="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/prefer-ledger.json" \
  --role review \
  --complexity-level medium \
  --detected-agents codex,agy,claude)"
# Pick a viable-but-not-default candidate to prove the preference changes the winner.
prefer_target="$(python3 -c 'import json,sys; p=json.loads(sys.argv[1]); print(p["evaluated_candidates"][-1]["model"])' "${prefer_base}")"
prefer_default="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["model"])' "${prefer_base}")"
[[ "${prefer_target}" != "${prefer_default}" ]] || fail "test setup: need a non-default candidate to prefer"

prefer_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/prefer-ledger.json" \
  --role review \
  --complexity-level medium \
  --detected-agents codex,agy,claude \
  --prefer-model "${prefer_target}")"
assert_json_field "${prefer_output}" 'payload["model"] == "'"${prefer_target}"'"' "prefer-model is honored when the model is a live candidate"
assert_json_field "${prefer_output}" 'payload["selection_reason"] == "sticky-reviewer-preference"' "router reports the sticky reviewer reason"

# Excluding the preferred model (artifact retry) must override the preference and fall back.
prefer_excluded="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/prefer-ledger.json" \
  --role review \
  --complexity-level medium \
  --detected-agents codex,agy,claude \
  --prefer-model "${prefer_target}" \
  --exclude-model "${prefer_target}")"
assert_json_field "${prefer_excluded}" 'payload["model"] != "'"${prefer_target}"'"' "excluded model overrides prefer-model"
assert_json_field "${prefer_excluded}" 'payload["selection_reason"] != "sticky-reviewer-preference"' "router falls back when the preferred model is excluded"

# A preference for a model that is not a viable candidate must fall back gracefully.
prefer_missing="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/prefer-ledger.json" \
  --role review \
  --complexity-level medium \
  --detected-agents codex,agy,claude \
  --prefer-model definitely-not-a-real-model)"
assert_json_field "${prefer_missing}" 'payload["selection_reason"] != "sticky-reviewer-preference"' "unknown preferred model falls back without error"

echo "PASS: router honors sticky reviewer preference"

record_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/record-ledger.json" \
  --role impl \
  --complexity-level easy \
  --detected-agents codex,agy,github-copilot,claude \
  --record)"

assert_json_field "${record_output}" 'payload["ledger"]["updated"] is True' "record mode reports ledger update"
python3 - "${WORK_DIR}/record-ledger.json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    ledger = json.load(handle)

if len(ledger.get("decisions", [])) != 1:
    raise SystemExit("recorded ledger must include one decision")
decision = ledger["decisions"][0]
agent = decision["agent"]
if ledger["totals"]["agents"].get(agent) != 1:
    raise SystemExit("recorded ledger total for selected agent must be 1")
PY

echo "PASS: router records selected usage"

QUEUE_LEDGER="${WORK_DIR}/queue-ledger.json"
levels=(easy medium medium-hard complex easy medium medium-hard complex)
for i in "${!levels[@]}"; do
  level="${levels[$i]}"
  "${ROUTER_PATH}" \
    --registry-file "${REGISTRY_PATH}" \
    --ledger-file "${QUEUE_LEDGER}" \
    --role complexity \
    --complexity-level "${level}" \
    --detected-agents codex,agy,github-copilot,claude \
    --record >/dev/null
  impl_output="$("${ROUTER_PATH}" \
    --registry-file "${REGISTRY_PATH}" \
    --ledger-file "${QUEUE_LEDGER}" \
    --role impl \
    --complexity-level "${level}" \
    --detected-agents codex,agy,github-copilot,claude \
    --record)"
  impl_model="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["model"])' "${impl_output}")"
  "${ROUTER_PATH}" \
    --registry-file "${REGISTRY_PATH}" \
    --ledger-file "${QUEUE_LEDGER}" \
    --role review \
    --complexity-level "${level}" \
    --exclude-model "${impl_model}" \
    --detected-agents codex,agy,github-copilot,claude \
    --record >/dev/null
  if [[ $((i % 3)) -eq 2 ]]; then
    "${ROUTER_PATH}" \
      --registry-file "${REGISTRY_PATH}" \
      --ledger-file "${QUEUE_LEDGER}" \
      --role modify \
      --complexity-level "${level}" \
      --detected-agents codex,agy,github-copilot,claude \
      --record >/dev/null
  fi
done

python3 - "${QUEUE_LEDGER}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    ledger = json.load(handle)

counts = ledger["totals"]["agents"]
total = sum(counts.values())
if counts.get("github-copilot", 0) != 0:
    raise SystemExit("GitHub Copilot must not be selected in mixed dummy queue")
percent = {agent: counts.get(agent, 0) / total * 100 for agent in ("codex", "agy", "claude")}
complexity_counts = ledger["totals"]["roles"]["complexity"]["agents"]
complexity_total = sum(complexity_counts.values())
complexity_agy_percent = complexity_counts.get("agy", 0) / complexity_total * 100

if percent["codex"] < 45:
    raise SystemExit(f"Codex should stay near the 60 percent overall target, saw {percent['codex']:.1f}%")
if percent["claude"] < 20:
    raise SystemExit(f"Claude should increase toward the 35 percent overall target, saw {percent['claude']:.1f}%")
if percent["agy"] > 30:
    raise SystemExit(f"Agy should stay bounded outside complexity scoring, saw {percent['agy']:.1f}% overall")
if not (35 <= complexity_agy_percent <= 65):
    raise SystemExit(f"Agy should take roughly half of complexity scoring, saw {complexity_agy_percent:.1f}%")
PY

echo "PASS: mixed dummy queue tracks updated subscription targets"
