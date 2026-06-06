#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTER_PATH="${ROOT_DIR}/assets/python/run-with-it-router.py"
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

assert_file_exists "${ROUTER_PATH}" "router helper exists"
[[ -x "${ROUTER_PATH}" ]] || fail "router helper is executable"

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

python3 - "${REGISTRY_PATH}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    registry = json.load(handle)

distribution = registry["model_routing"]["usage_distribution"]
target = distribution["default_target_percent"]
expected = {
    "codex": 50,
    "agy": 20,
    "github-copilot": 20,
    "claude": 10,
}
if target != expected:
    raise SystemExit(f"default target mismatch: {target!r}")
if sum(target.values()) != 100:
    raise SystemExit("default target must sum to 100")
for role in ("complexity", "impl", "review", "modify", "merge-recovery"):
    if role not in distribution["role_target_percent"]:
        raise SystemExit(f"missing role target for {role}")
PY

echo "PASS: registry declares subscription usage distribution"

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

codex_heavy_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/codex-heavy-ledger.json" \
  --role impl \
  --complexity-level easy \
  --detected-agents codex,agy,github-copilot,claude)"

assert_json_field "${codex_heavy_output}" 'payload["agent"] in {"agy", "github-copilot"}' "codex-heavy ledger shifts easy implementation away from Codex"
assert_json_field "${codex_heavy_output}" 'payload["policy"]["default_target_percent"]["codex"] == 50' "router reports Codex 50 percent default target"
assert_json_field "${codex_heavy_output}" 'payload["ledger"]["updated"] is False' "router does not update ledger unless requested"

echo "PASS: router shifts easy work away from over-target Codex"

complexity_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/empty-ledger.json" \
  --role complexity \
  --complexity-level medium \
  --detected-agents codex,agy,github-copilot,claude)"

assert_json_field "${complexity_output}" 'payload["agent"] == "agy"' "complexity scoring prefers Agy when all tools are available"
assert_json_field "${complexity_output}" 'payload["model"] in {"gemini-3.1-pro-low", "gemini-3.1-pro-high", "gemini-3.5-flash-medium"}' "complexity scoring picks an easy-medium Agy model"

echo "PASS: router protects scarce tools during complexity scoring"

review_output="$("${ROUTER_PATH}" \
  --registry-file "${REGISTRY_PATH}" \
  --ledger-file "${WORK_DIR}/review-ledger.json" \
  --role review \
  --complexity-level medium-hard \
  --exclude-model gpt-5.3-codex \
  --detected-agents codex,agy,github-copilot,claude)"

assert_json_field "${review_output}" 'payload["model"] != "gpt-5.3-codex"' "review excludes implementation model"
assert_json_field "${review_output}" 'payload["agent"] in {"codex", "claude", "github-copilot"}' "review avoids Agy unless higher-priority review tools are unavailable"

echo "PASS: router selects independent review model"

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
percent = {agent: counts.get(agent, 0) / total * 100 for agent in ("codex", "agy", "github-copilot", "claude")}

if percent["codex"] < 45:
    raise SystemExit(f"Codex should stay near the 50 percent overall target, saw {percent['codex']:.1f}%")
if not 15 <= percent["agy"] <= 30:
    raise SystemExit(f"Agy should stay near the 20 percent overall target, saw {percent['agy']:.1f}%")
if percent["claude"] > 15:
    raise SystemExit(f"Claude should be protected near the 10 percent overall target, saw {percent['claude']:.1f}%")
PY

echo "PASS: mixed dummy queue stays near overall subscription targets"
