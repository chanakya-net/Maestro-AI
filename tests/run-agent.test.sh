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
provider_rules = registry.get("model_routing", {}).get("provider_routing_rules", {})
required_agents = ["codex", "claude", "gemini", "github-copilot", "opencode"]

for agent_id in required_agents:
    check(agent_id in agents, f"missing agent entry: {agent_id}")

check(aliases.get("copilot") == "github-copilot", "copilot alias resolves to github-copilot")
check(aliases.get("claude-code") == "claude", "claude-code alias resolves to claude")
check(aliases.get("open-code") == "opencode", "open-code alias resolves to opencode")
check(aliases.get("open_code") == "opencode", "open_code alias resolves to opencode")

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

for agent_id in ["codex", "claude", "gemini", "github-copilot"]:
    model = agents[agent_id]["model"]
    check(model.get("default"), f"{agent_id} has default model metadata")
    check(model.get("known_models"), f"{agent_id} has known model metadata")
    check(agents[agent_id]["user_model_configuration"]["requires_user_model_config"] is False, f"{agent_id} does not require user model config")

google_rules = provider_rules.get("google", {})
check(google_rules.get("automatic_routing") == "last_resort_only", "google provider is last-resort-only for automatic routing")
check("last-resort" in google_rules.get("_note", "").lower(), "google provider note documents last-resort routing")
check("explicit" in google_rules.get("_note", "").lower(), "google provider note documents explicit overrides")

gemini_known_models = set(agents["gemini"]["model"].get("known_models", []))
removed_gemini_models = {
    "auto-gemini-2.5",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
}
check(not removed_gemini_models & set(model_catalog), "gemini 2.5 models are removed from model catalog")
check(not removed_gemini_models & gemini_known_models, "gemini 2.5 models are removed from known models")
for model_id in ["auto-gemini-3", "gemini-3.1-pro-preview", "gemini-3.1-pro-preview-customtools", "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview"]:
    check(model_id in gemini_known_models, f"{model_id} remains in gemini known models")
for model_id in ["auto", "pro", "flash", "flash-lite"]:
    check(model_id in gemini_known_models, f"{model_id} generic gemini alias remains in known models")

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

gemini_dry_run_output="$("${RUNNER_PATH}" --agent gemini --model auto-gemini-3 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${gemini_dry_run_output}" "gemini --model auto-gemini-3 --prompt" "gemini dry-run uses prompt flags without elevated permission mode"
assert_not_contains "${gemini_dry_run_output}" "--yolo" "gemini dry-run excludes yolo mode by default"
assert_not_contains "${gemini_dry_run_output}" "--approval-mode=yolo" "gemini dry-run excludes approval-mode yolo by default"
assert_not_contains "${gemini_dry_run_output}" "--consent" "gemini dry-run excludes unsupported consent flag"

claude_dry_run_output="$("${RUNNER_PATH}" --agent claude --model claude-sonnet-4-6 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${claude_dry_run_output}" "claude --dangerously-skip-permissions --model claude-sonnet-4-6 --print" "claude dry-run uses supported print/model/permission flags"

copilot_dry_run_output="$("${RUNNER_PATH}" --agent github-copilot --model gpt-5.5 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${copilot_dry_run_output}" "copilot --allow-all-tools --model gpt-5.5 -p" "copilot dry-run uses supported prompt/model/permission flags"

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

mkdir -p "${WORK_DIR}/empty-path"
set +e
parser_output="$(PATH="${WORK_DIR}/empty-path" "${BASH}" "${RUNNER_PATH}" --list-agents 2>&1)"
parser_status=$?
set -e

assert_equals "1" "${parser_status}" "runner fails when no JSON parser is available"
assert_contains "${parser_output}" "install jq or python3" "missing parser failure is clear"

echo "PASS: run-agent fails clearly without a JSON parser"
