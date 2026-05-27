#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY_PATH="${ROOT_DIR}/assets/agent-registry.json"
RUNNER_PATH="${ROOT_DIR}/assets/run-agent.sh"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  local message="$3"

  if [[ "${expected}" != "${actual}" ]]; then
    fail "${message} (expected: ${expected}, actual: ${actual})"
  fi
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"

  if [[ "${haystack}" != *"${needle}"* ]]; then
    fail "${message} (missing: ${needle})"
  fi
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"

  if [[ "${haystack}" == *"${needle}"* ]]; then
    fail "${message} (unexpected: ${needle})"
  fi
}

if [[ ! -f "${REGISTRY_PATH}" ]]; then
  fail "agent registry exists at assets/agent-registry.json"
fi

if [[ ! -x "${RUNNER_PATH}" ]]; then
  fail "run-agent.sh exists and is executable"
fi

prompt_contract="$(<"${ROOT_DIR}/assets/prompt.md")"
assert_contains "${prompt_contract}" "## Progress Visibility" "implementation prompt documents progress visibility"
assert_contains "${prompt_contract}" "Do not emit periodic heartbeat or status-check lines while working." "implementation prompt keeps workers focused"
assert_not_contains "${prompt_contract}" "STATUS|type=heartbeat|issue=<issue-or-unknown>|role=impl" "implementation prompt does not request child heartbeat output"
assert_not_contains "${prompt_contract}" "at least once every 60 seconds" "implementation prompt does not request heartbeat cadence"
assert_contains "${prompt_contract}" 'RUN_WITH_IT_RESULT_FILE' "implementation prompt names dispatcher result path"
assert_contains "${prompt_contract}" 'RUN_WITH_IT_DONE_FILE' "implementation prompt names dispatcher done path"

modifier_contract="$(<"${ROOT_DIR}/assets/modifier-prompt.md")"
assert_contains "${modifier_contract}" 'RUN_WITH_IT_RESULT_FILE' "modifier prompt names dispatcher result path"
assert_contains "${modifier_contract}" 'RUN_WITH_IT_DONE_FILE' "modifier prompt names dispatcher done path"

review_contract="$(<"${ROOT_DIR}/assets/review-prompt.md")"
assert_contains "${review_contract}" 'RUN_WITH_IT_RESULT_FILE points to REVIEWER_STATUS_FILE' "review prompt ties dispatcher result path to reviewer status JSON"
assert_contains "${review_contract}" 'RUN_WITH_IT_DONE_FILE' "review prompt names dispatcher done path"

complexity_contract="$(<"${ROOT_DIR}/assets/complexity-prompt.md")"
assert_contains "${complexity_contract}" 'The only file you may write is RUN_WITH_IT_RESULT_FILE' "complexity prompt permits required dispatcher result artifact"
assert_contains "${complexity_contract}" 'RUN_WITH_IT_DONE_FILE is runner-owned' "complexity prompt keeps done file runner-owned"

runner_preamble="$(sed -n '1,8p' "${RUNNER_PATH}")"
assert_not_contains "${runner_preamble}" "set -euo pipefail" "run-agent avoids nounset so VS Code zsh prompt hooks are not tripped"
assert_not_contains "${runner_preamble}" "set -u" "run-agent avoids nounset shorthand"
assert_not_contains "${runner_preamble}" "set -o nounset" "run-agent avoids nounset long form"

python3 - "${REGISTRY_PATH}" <<'PY'
import json
import sys

path = sys.argv[1]

with open(path, "r", encoding="utf-8") as handle:
    registry = json.load(handle)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


agents = registry.get("agents", {})
aliases = registry.get("aliases", {})
model_catalog = registry.get("model_catalog", {})
model_routing = registry.get("model_routing", {})
provider_rules = model_routing.get("provider_routing_rules", {})
required_agents = ["codex", "claude", "github-copilot", "opencode", "agy"]

for agent_id in required_agents:
    check(agent_id in agents, f"missing agent entry: {agent_id}")
check("gemini" not in agents, "standalone gemini agent is removed; route Google models through agy")

check(aliases.get("copilot") == "github-copilot", "copilot alias resolves to github-copilot")
check(aliases.get("claude-code") == "claude", "claude-code alias resolves to claude")
check(aliases.get("open_code") == "opencode", "open_code alias resolves to opencode")
check(aliases.get("gemini-cli") is None, "gemini-cli alias is removed with standalone gemini agent")

for agent_id, agent in agents.items():
    check(agent.get("display_name"), f"{agent_id} has display_name")
    check(agent.get("detection", {}).get("command"), f"{agent_id} has detection command")
    check(agent.get("invocation", {}).get("command"), f"{agent_id} has invocation command")
    check(agent.get("invocation", {}).get("prompt_argument_template"), f"{agent_id} has prompt argument template")
    check(agent.get("permission_modes"), f"{agent_id} has permission modes")
    check(agent.get("model", {}).get("flag_template") is not None, f"{agent_id} has model flag template")
    check(agent.get("capability_band") in {"fast", "balanced", "advanced"}, f"{agent_id} has capability band")
    check(isinstance(agent.get("fallback_order"), list), f"{agent_id} has fallback order")
    check("requires_user_model_config" in agent.get("user_model_configuration", {}), f"{agent_id} declares user model config behavior")

for agent_id in ["claude", "github-copilot", "agy"]:
    model = agents[agent_id]["model"]
    check(model.get("default"), f"{agent_id} has default model metadata")
    check(model.get("known_models"), f"{agent_id} has known model metadata")
    check(agents[agent_id]["user_model_configuration"]["requires_user_model_config"] is False, f"{agent_id} does not require user model config")

codex_model = agents["codex"]["model"]
check(codex_model.get("default") == "", "codex does not pin a default model")
check(codex_model.get("known_models"), "codex has known model metadata")
check(agents["codex"]["user_model_configuration"]["requires_user_model_config"] is False, "codex does not require user model config")

check(model_routing.get("cost_basis") is None, "subscription routing no longer carries API cost basis")
for model_id, catalog_entry in model_catalog.items():
    for removed_key in ("price_input_per_1m", "price_output_per_1m", "price_tier"):
        check(removed_key not in catalog_entry, f"{model_id} omits {removed_key} under subscription routing")

expected_codex_models = [
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
]
check(codex_model.get("known_models") == expected_codex_models, "codex known models match available Codex model list")
check(codex_model.get("pricing_basis") == "subscription", "codex declares subscription pricing basis")
check(codex_model.get("metered_api_cost") is False, "codex is not treated as API-metered")
for model_id in expected_codex_models:
    check(model_id in model_catalog, f"codex model catalog includes {model_id}")
check("routing_cost_overrides" not in codex_model, "codex model metadata omits cost overrides")

claude_model = agents["claude"]["model"]
expected_claude_models = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
]
check(claude_model.get("default") == "claude-sonnet-4-6", "claude defaults to selected balanced model")
check(claude_model.get("known_models") == expected_claude_models, "claude known models match available Claude model list")
check(claude_model.get("pricing_basis") == "subscription", "claude declares subscription pricing basis")
check(claude_model.get("metered_api_cost") is False, "claude is not treated as API-metered")
for model_id in expected_claude_models:
    check(model_id in model_catalog, f"claude model catalog includes {model_id}")
check("routing_cost_overrides" not in claude_model, "claude model metadata omits cost overrides")

copilot_model = agents["github-copilot"]["model"]
expected_copilot_models = [
    "claude-haiku-4.5",
    "claude-sonnet-4.6",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.3-codex",
    "gpt-5.4",
    "gpt-5.4-mini",
]
check(copilot_model.get("default") == "gpt-5.3-codex", "github-copilot defaults to 1x coding model")
check(copilot_model.get("known_models") == expected_copilot_models, "github-copilot known models match available Copilot model list")
for model_id in expected_copilot_models:
    check(model_id in model_catalog, f"copilot model catalog includes {model_id}")
check("claude-sonnet-4.6" in copilot_model.get("known_models", []), "Copilot-specific Claude model ID is preserved")
check("claude-sonnet-4-6" in claude_model.get("known_models", []), "Claude-specific Claude model ID is preserved")

google_rules = provider_rules.get("google", {})
check(google_rules.get("automatic_routing") == "all", "google provider can route through agy for all bands")
check(google_rules.get("preferred_agents") == ["agy"], "google provider routes through agy")
check(google_rules.get("fallback_agents") == ["agy"], "google provider falls back through agy only")
check("agy" in google_rules.get("_note", "").lower(), "google provider note documents agy routing")

agy_model = agents["agy"]["model"]
expected_agy_models = [
    "gemini-3.5-flash-high",
    "gemini-3.5-flash-medium",
    "gemini-3.1-pro-low",
    "gemini-3.1-pro-high",
    "claude-sonnet-4.6-thinking",
    "claude-opus-4.6-thinking",
    "gpt-0ss-120b-medium",
]
check(agy_model.get("default") == "gemini-3.5-flash-high", "agy defaults to high Gemini model")
check(agy_model.get("known_models") == expected_agy_models, "agy known models match available Agy model list")
for model_id in expected_agy_models:
    check(model_id in model_catalog, f"agy model catalog includes {model_id}")

anthropic_rules = provider_rules.get("anthropic", {})
check(anthropic_rules.get("automatic_routing") == "all", "direct Claude routing remains automatic")
check(anthropic_rules.get("preferred_agents") == ["github-copilot", "claude"], "Copilot and Claude are preferred for Claude-provider models")
check(anthropic_rules.get("fallback_agents") == ["claude"], "direct Claude is the fallback agent for Claude-provider models")

agent_preference_rules = model_routing.get("agent_preference_rules", [])
haiku_rules = [rule for rule in agent_preference_rules if any("haiku" in m for m in rule.get("models", []))]
check(haiku_rules, "Haiku has explicit agent preference rule")
check(haiku_rules[0].get("preferred_agents") == ["github-copilot", "claude"], "Haiku prefers Copilot before Claude")
anthropic_agent_rules = [rule for rule in agent_preference_rules if rule.get("provider") == "anthropic"]
check(anthropic_agent_rules, "anthropic provider has explicit agent preference rule")
check(anthropic_agent_rules[0].get("automatic_routing") == "all", "anthropic direct Claude rule remains automatic")

for agent_id, agent in agents.items():
    fallback_order = agent.get("fallback_order", [])
    check("gemini" not in fallback_order, f"{agent_id} fallback order does not reference removed gemini agent")
    if agent_id != "claude" and "claude" in fallback_order:
        check(fallback_order[-1] == "claude", f"{agent_id} uses direct Claude only after other fallback agents")

agy_known_models = set(agents["agy"]["model"].get("known_models", []))
removed_gemini_catalog_models = {
    "auto-gemini-2.5",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
}
removed_gemini_cli_models = {
    *removed_gemini_catalog_models,
    "gemini-2.5-pro",
}
check(not removed_gemini_catalog_models & set(model_catalog), "old gemini 2.5 flash models are removed from model catalog")
check(not removed_gemini_cli_models & agy_known_models, "gemini 2.5 models are removed from Agy known models")
for model_id in ["gemini-3.5-flash-high", "gemini-3.5-flash-medium", "gemini-3.1-pro-low", "gemini-3.1-pro-high"]:
    check(model_id in agy_known_models, f"{model_id} remains in agy known models")

opencode = agents["opencode"]
check(opencode["user_model_configuration"]["requires_user_model_config"] is True, "opencode requires user model config")
check(opencode["user_model_configuration"].get("config_paths"), "opencode documents config paths")
check(opencode["user_model_configuration"].get("skip_when_unconfigured") is True, "opencode is skipped when unconfigured")
check("detected" in opencode["user_model_configuration"].get("skip_message", "").lower(), "opencode skip message mentions detected state")
check("model" in opencode["user_model_configuration"].get("skip_message", "").lower(), "opencode skip message mentions missing model config")

print("PASS: agent registry contract")
PY

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT
export GUI_MODE=0

CONTEXT_FILE="${WORK_DIR}/context.md"
PROMPT_FILE="${WORK_DIR}/prompt.md"
FAKE_BIN="${WORK_DIR}/bin"
CUSTOM_REGISTRY="${WORK_DIR}/registry.json"
mkdir -p "${FAKE_BIN}"
printf 'Issue context\n' > "${CONTEXT_FILE}"
printf 'Do the work\n' > "${PROMPT_FILE}"
printf '#!/usr/bin/env bash\nprintf "fake-agent 1.0\\n"\n' > "${FAKE_BIN}/fake-agent"
chmod +x "${FAKE_BIN}/fake-agent"

cat > "${CUSTOM_REGISTRY}" <<JSON
{
  "schema_version": 1,
  "aliases": {
    "fake-alias": "fake"
  },
  "agents": {
    "fake": {
      "display_name": "Fake Agent",
      "detection": {
        "command": "fake-agent",
        "args": ["--version"]
      },
      "invocation": {
        "command": "fake-agent",
        "args_template": ["run", "{{permission_mode}}", "{{model_flag}}", "{{extra_args}}", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": {
        "default": "--unsafe",
        "available": ["--unsafe"]
      },
      "model": {
        "default": "fake-default",
        "flag_template": "--model {{model}}",
        "known_models": ["fake-default", "fake-pro"]
      },
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {
        "requires_user_model_config": false,
        "config_paths": [],
        "skip_when_unconfigured": false,
        "skip_message": ""
      }
    },
    "failing": {
      "display_name": "Failing Agent",
      "detection": {
        "command": "failing-agent",
        "args": ["--version"]
      },
      "invocation": {
        "command": "failing-agent",
        "args_template": ["{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": {
        "default": "",
        "available": [""]
      },
      "model": {
        "default": "",
        "flag_template": "--model {{model}}",
        "known_models": []
      },
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {
        "requires_user_model_config": false,
        "config_paths": [],
        "skip_when_unconfigured": false,
        "skip_message": ""
      }
    },
    "empty-models": {
      "display_name": "Empty Models",
      "detection": {
        "command": "missing-empty-models",
        "args": ["--version"]
      },
      "invocation": {
        "command": "missing-empty-models",
        "args_template": ["{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": {
        "default": "",
        "available": [""]
      },
      "model": {
        "default": "",
        "flag_template": "--model {{model}}",
        "known_models": []
      },
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {
        "requires_user_model_config": false,
        "config_paths": [],
        "skip_when_unconfigured": false,
        "skip_message": ""
      }
    }
  }
}
JSON

printf '#!/usr/bin/env bash\nexit 7\n' > "${FAKE_BIN}/failing-agent"
chmod +x "${FAKE_BIN}/failing-agent"

OPENCODE_REGISTRY="${WORK_DIR}/opencode-registry.json"
cat > "${OPENCODE_REGISTRY}" <<'JSON'
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "opencode": {
      "display_name": "OpenCode",
      "detection": {
        "command": "opencode",
        "args": ["--version"]
      },
      "invocation": {
        "command": "opencode",
        "args_template": ["run", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": {
        "default": "",
        "available": [""]
      },
      "model": {
        "default": "",
        "flag_template": "--model {{model}}",
        "known_models": []
      },
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {
        "requires_user_model_config": true,
        "config_paths": [
          "$HOME/.config/opencode/opencode.json",
          "$HOME/.opencode.json",
          "./opencode.json"
        ],
        "skip_when_unconfigured": true,
        "skip_message": "OpenCode detected but no user model configuration was found; skipping OpenCode until a model is configured."
      }
    }
  }
}
JSON

dry_run_output="$("${RUNNER_PATH}" --agent codex --model gpt-5.3-codex --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${dry_run_output}" "codex exec" "dry-run prints codex command"
assert_contains "${dry_run_output}" "--model gpt-5.3-codex" "dry-run includes selected model"
assert_contains "${dry_run_output}" "--dangerously-bypass-approvals-and-sandbox" "dry-run includes unattended permission mode"
assert_not_contains "${dry_run_output}" "--ask-for-approval" "codex exec dry-run excludes unsupported approval flag"
assert_contains "${dry_run_output}" "Issue context" "dry-run includes combined payload"
assert_contains "${dry_run_output}" "Instructions:" "dry-run inserts instructions separator"
assert_contains "${dry_run_output}" "Do the work" "dry-run includes prompt file"

echo "PASS: run-agent dry-run builds command from CLI arguments"

EXTERNAL_ASSET_ROOT="${WORK_DIR}/external-assets"
mkdir -p "${EXTERNAL_ASSET_ROOT}"
cp "${RUNNER_PATH}" "${EXTERNAL_ASSET_ROOT}/run-agent.sh"
cp "${REGISTRY_PATH}" "${EXTERNAL_ASSET_ROOT}/agent-registry.json"
external_asset_dry_run_output="$("${EXTERNAL_ASSET_ROOT}/run-agent.sh" --agent codex --model gpt-5.3-codex --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${external_asset_dry_run_output}" "-C ${ROOT_DIR}" "runner defaults repo root to launch directory when assets live elsewhere"
assert_not_contains "${external_asset_dry_run_output}" "-C ${WORK_DIR}/external-assets" "runner does not treat installed asset directory as repo root"

echo "PASS: run-agent defaults repo root to launch directory"

codex_gui_dry_run_output="$(GUI_MODE=1 "${RUNNER_PATH}" --agent codex --model gpt-5.3-codex --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${codex_gui_dry_run_output}" "--sandbox=workspace-write" "GUI mode uses workspace-write sandbox for Codex"
assert_not_contains "${codex_gui_dry_run_output}" "--dangerously-bypass-approvals-and-sandbox" "GUI mode avoids Codex sandbox bypass"

codex_auto_gui_dry_run_output="$(GUI_MODE=auto TERM_PROGRAM=vscode "${RUNNER_PATH}" --agent codex --model gpt-5.3-codex --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${codex_auto_gui_dry_run_output}" "--sandbox=workspace-write" "GUI auto-detection uses workspace-write sandbox for Codex"

claude_gui_dry_run_output="$(GUI_MODE=1 "${RUNNER_PATH}" --agent claude --model claude-sonnet-4-6 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${claude_gui_dry_run_output}" "--permission-mode=acceptEdits" "GUI mode uses acceptEdits permission mode for Claude"
assert_not_contains "${claude_gui_dry_run_output}" "--dangerously-skip-permissions" "GUI mode avoids Claude permission bypass"

copilot_gui_dry_run_output="$(GUI_MODE=1 "${RUNNER_PATH}" --agent github-copilot --model gpt-5.5 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${copilot_gui_dry_run_output}" "--allow-all-tools" "GUI mode keeps Copilot non-interactive tool permission"
assert_not_contains "${copilot_gui_dry_run_output}" "--allow-all-paths" "GUI mode avoids Copilot all-paths permission"

echo "PASS: run-agent GUI mode selects safer non-interactive permissions"

agy_dry_run_output="$("${RUNNER_PATH}" --agent agy --model gemini-3.5-flash-high --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${agy_dry_run_output}" "agy " "agy dry-run uses agy command"
assert_not_contains "${agy_dry_run_output}" "--model" "agy dry-run lets AGY select the model automatically"
assert_contains "${agy_dry_run_output}" "--print" "agy dry-run uses print flag"
assert_contains "${agy_dry_run_output}" "--dangerously-skip-permissions" "agy dry-run includes registry default permission mode"

claude_dry_run_output="$("${RUNNER_PATH}" --agent claude --model claude-sonnet-4-6 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${claude_dry_run_output}" "claude --dangerously-skip-permissions --model claude-sonnet-4-6 --print" "claude dry-run uses supported print/model/permission flags"

copilot_dry_run_output="$("${RUNNER_PATH}" --agent github-copilot --model gpt-5.5 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${copilot_dry_run_output}" "copilot " "copilot dry-run uses copilot command"
assert_contains "${copilot_dry_run_output}" "--model gpt-5.5" "copilot dry-run forwards selected model"
assert_contains "${copilot_dry_run_output}" " -p " "copilot dry-run uses prompt flag"

opencode_dry_run_output="$("${RUNNER_PATH}" --agent opencode --model github-copilot/gpt-5.5 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${opencode_dry_run_output}" "opencode run --dangerously-skip-permissions --model github-copilot/gpt-5.5" "opencode dry-run places supported run permission flag after subcommand"

echo "PASS: run-agent registry uses supported CLI flags"

env_dry_run_output="$(PATH="${FAKE_BIN}:${PATH}" AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" AGENT=fake-alias MODEL=fake-pro CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" PROMPT_FILE="${PROMPT_FILE}" UNATTENDED=1 "${RUNNER_PATH}" --dry-run)"
assert_contains "${env_dry_run_output}" "fake-agent run" "environment variables select custom registry agent"
assert_contains "${env_dry_run_output}" "--model fake-pro" "environment MODEL selects model"
assert_contains "${env_dry_run_output}" "--unsafe" "environment dry-run includes default permission mode"

echo "PASS: run-agent supports environment argument parity"

successful_run_stdout="${WORK_DIR}/successful-run.stdout"
successful_run_stderr="${WORK_DIR}/successful-run.stderr"
PATH="${FAKE_BIN}:${PATH}" AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" AGENT=fake CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" PROMPT_FILE="${PROMPT_FILE}" UNATTENDED=1 "${RUNNER_PATH}" >"${successful_run_stdout}" 2>"${successful_run_stderr}"
successful_run_output="$(<"${successful_run_stdout}")"
successful_run_telemetry="$(<"${successful_run_stderr}")"
assert_equals "fake-agent 1.0" "${successful_run_output}" "successful invocation preserves agent stdout"
assert_contains "${successful_run_telemetry}" "STATUS|type=telemetry|agent=fake|model=fake-default|input_tokens=unknown|output_tokens=unknown|cache_hit_tokens=unknown|status=success|source=runner-default" "successful invocation emits normalized telemetry"

set +e
failing_run_stdout="${WORK_DIR}/failing-run.stdout"
failing_run_stderr="${WORK_DIR}/failing-run.stderr"
PATH="${FAKE_BIN}:${PATH}" AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" AGENT=failing CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" PROMPT_FILE="${PROMPT_FILE}" UNATTENDED=1 "${RUNNER_PATH}" >"${failing_run_stdout}" 2>"${failing_run_stderr}"
failing_run_status=$?
set -e
failing_run_output="$(<"${failing_run_stdout}")"
failing_run_telemetry="$(<"${failing_run_stderr}")"
assert_equals "7" "${failing_run_status}" "runner preserves agent exit status"
assert_equals "" "${failing_run_output}" "failing invocation preserves empty stdout"
assert_contains "${failing_run_telemetry}" "STATUS|type=telemetry|agent=failing|model=unknown|input_tokens=unknown|output_tokens=unknown|cache_hit_tokens=unknown|status=failed|source=runner-default" "failed invocation emits normalized telemetry"

echo "PASS: run-agent emits normalized telemetry contract"

print_prompt_output="$(AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" AGENT=fake CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" PROMPT_FILE="${PROMPT_FILE}" PRINT_PROMPT=1 "${RUNNER_PATH}")"
assert_equals $'Issue context\n\nInstructions:\n\nDo the work' "${print_prompt_output}" "PRINT_PROMPT prints combined payload and exits"

echo "PASS: run-agent prints combined payload"

list_output="$(PATH="${FAKE_BIN}:${PATH}" AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" "${RUNNER_PATH}" --list-agents)"
assert_contains "${list_output}" $'fake\tFake Agent\tdetected\tdetected' "list-agents shows detected custom agent"
assert_contains "${list_output}" $'empty-models\tEmpty Models\tmissing\tmissing command: missing-empty-models' "list-agents shows missing command reason"

detected_only_output="$(PATH="${FAKE_BIN}:${PATH}" AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" "${RUNNER_PATH}" --list-agents --detected-only)"
assert_contains "${detected_only_output}" $'fake\tFake Agent\tdetected\tdetected' "detected-only keeps detected custom agent"
if [[ "${detected_only_output}" == *"empty-models"* ]]; then
  fail "detected-only filters missing agents"
fi

echo "PASS: run-agent lists detected and missing agents"

OPENCODE_HOME="${WORK_DIR}/opencode-home"
mkdir -p "${OPENCODE_HOME}"
printf '#!/usr/bin/env bash\nprintf "opencode 0.0.0\\n"\n' > "${FAKE_BIN}/opencode"
chmod +x "${FAKE_BIN}/opencode"

opencode_list_output="$(HOME="${OPENCODE_HOME}" PATH="${FAKE_BIN}:${PATH}" AGENT_REGISTRY_FILE="${OPENCODE_REGISTRY}" "${RUNNER_PATH}" --list-agents)"
assert_contains "${opencode_list_output}" $'opencode\tOpenCode\tmissing\tOpenCode detected but no user model configuration was found; skipping OpenCode until a model is configured.' "list-agents explains missing OpenCode model configuration"

opencode_detected_only_output="$(HOME="${OPENCODE_HOME}" PATH="${FAKE_BIN}:${PATH}" AGENT_REGISTRY_FILE="${OPENCODE_REGISTRY}" "${RUNNER_PATH}" --list-agents --detected-only)"
if [[ -n "${opencode_detected_only_output}" ]]; then
  fail "detected-only omits OpenCode when model config is missing"
fi

echo "PASS: run-agent reports OpenCode model-configuration gating"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_FALLBACK_BIN="${WORK_DIR}/python-fallback-bin"
  mkdir -p "${PYTHON_FALLBACK_BIN}"
  ln -s "$(command -v python3)" "${PYTHON_FALLBACK_BIN}/python3"
  ln -s "${FAKE_BIN}/fake-agent" "${PYTHON_FALLBACK_BIN}/fake-agent"

  python_fallback_output="$(PATH="${PYTHON_FALLBACK_BIN}" AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" "${BASH}" "${RUNNER_PATH}" --list-agents --detected-only)"
  assert_contains "${python_fallback_output}" $'fake\tFake Agent\tdetected\tdetected' "python3 parser fallback lists detected agents when jq is unavailable"

  echo "PASS: run-agent falls back to python3 JSON parsing"
fi

if command -v python3 >/dev/null 2>&1; then
  GUI_HOME="${WORK_DIR}/gui-home"
  mkdir -p "${GUI_HOME}/.local/bin"
  ln -s "$(command -v python3)" "${GUI_HOME}/.local/bin/python3"
  printf '#!/usr/bin/env bash\nprintf "fake-agent 1.0\\n"\n' > "${GUI_HOME}/.local/bin/fake-agent"
  chmod +x "${GUI_HOME}/.local/bin/fake-agent"

  gui_path_output="$(HOME="${GUI_HOME}" PATH="/usr/bin:/bin" AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" "${BASH}" "${RUNNER_PATH}" --list-agents --detected-only)"
  assert_contains "${gui_path_output}" $'fake\tFake Agent\tdetected\tdetected' "PATH bootstrap detects agents installed in GUI-thin user bin paths"

  echo "PASS: run-agent bootstraps PATH for GUI-launched environments"
fi

models_output="$(AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" "${RUNNER_PATH}" --list-models fake)"
assert_equals $'fake-default\nfake-pro' "${models_output}" "list-models prints configured models"

empty_models_output="$(AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" "${RUNNER_PATH}" --list-models empty-models)"
assert_equals "No configured models for empty-models." "${empty_models_output}" "list-models prints clear message without configured models"

echo "PASS: run-agent lists models"

set +e
unattended_output="$(AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" AGENT=fake CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" PROMPT_FILE="${PROMPT_FILE}" "${RUNNER_PATH}" --dry-run 2>&1)"
unattended_status=$?
set -e

assert_equals "1" "${unattended_status}" "unsafe permission mode requires unattended"
assert_contains "${unattended_output}" "requires --unattended or UNATTENDED=1" "unsafe permission failure is clear"

echo "PASS: run-agent rejects unsafe manual execution"

LARGE_PROMPT_FILE="${WORK_DIR}/large-prompt.md"
python3 - "${LARGE_PROMPT_FILE}" <<'PY'
import sys
path = sys.argv[1]
with open(path, "w", encoding="utf-8") as handle:
    handle.write("x" * 131073)
PY

large_prompt_stderr="${WORK_DIR}/large-prompt.stderr"
"${RUNNER_PATH}" --agent codex --model gpt-5.3-codex --context-file "${CONTEXT_FILE}" --prompt-file "${LARGE_PROMPT_FILE}" --dry-run --unattended >/dev/null 2>"${large_prompt_stderr}"
large_prompt_warning="$(<"${large_prompt_stderr}")"
assert_contains "${large_prompt_warning}" "warn: prompt exceeds 128KB" "large inline prompts emit a sandbox truncation warning"

echo "PASS: run-agent warns for large inline prompts"

mkdir -p "${WORK_DIR}/empty-path"
set +e
parser_output="$(RUN_AGENT_BOOTSTRAP_PATH=0 PATH="${WORK_DIR}/empty-path" "${BASH}" "${RUNNER_PATH}" --list-agents 2>&1)"
parser_status=$?
set -e

assert_equals "1" "${parser_status}" "runner fails when no JSON parser is available"
assert_contains "${parser_output}" "install jq or python3" "missing parser failure is clear"

echo "PASS: run-agent fails clearly without a JSON parser"
