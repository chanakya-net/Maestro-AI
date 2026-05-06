#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY_PATH="${ROOT_DIR}/assets/agent-registry.json"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

if [[ ! -f "${REGISTRY_PATH}" ]]; then
  fail "agent registry exists at assets/agent-registry.json"
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

opencode = agents["opencode"]
check(opencode["user_model_configuration"]["requires_user_model_config"] is True, "opencode requires user model config")
check(opencode["user_model_configuration"].get("config_paths"), "opencode documents config paths")
check(opencode["user_model_configuration"].get("skip_when_unconfigured") is True, "opencode is skipped when unconfigured")
check("detected" in opencode["user_model_configuration"].get("skip_message", "").lower(), "opencode skip message mentions detected state")
check("model" in opencode["user_model_configuration"].get("skip_message", "").lower(), "opencode skip message mentions missing model config")

print("PASS: agent registry contract")
PY
