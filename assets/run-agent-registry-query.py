#!/usr/bin/env python3
"""Query the agent registry JSON file from shell scripts.

Usage: run-agent-registry-query.py <registry_file> <action> [arg]

Actions: normalize, agents, exists, display, detect_command, detect_args,
         invoke_command, args_template, default_permission, default_model,
         model_flag_template, known_models, requires_config, skip_unconfigured,
         skip_message, config_paths
"""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(f"usage: {sys.argv[0]} <registry_file> <action> [arg]")

    path, action = sys.argv[1], sys.argv[2]
    arg = sys.argv[3] if len(sys.argv) > 3 else ""

    with open(path, "r", encoding="utf-8") as handle:
        registry = json.load(handle)

    agents = registry.get("agents", {})
    aliases = registry.get("aliases", {})

    def agent_id(value: str) -> str:
        return aliases.get(value, value)

    def agent(value: str) -> dict:
        return agents.get(agent_id(value), {})

    if action == "normalize":
        print(agent_id(arg))
    elif action == "agents":
        for key in agents:
            print(key)
    elif action == "exists":
        sys.exit(0 if agent_id(arg) in agents else 1)
    elif action == "display":
        print(agent(arg).get("display_name", ""))
    elif action == "detect_command":
        print(agent(arg).get("detection", {}).get("command", ""))
    elif action == "detect_args":
        for item in agent(arg).get("detection", {}).get("args", []):
            print(item)
    elif action == "invoke_command":
        print(agent(arg).get("invocation", {}).get("command", ""))
    elif action == "args_template":
        for item in agent(arg).get("invocation", {}).get("args_template", []):
            print(item)
    elif action == "default_permission":
        print(agent(arg).get("permission_modes", {}).get("default", ""))
    elif action == "default_model":
        print(agent(arg).get("model", {}).get("default", ""))
    elif action == "model_flag_template":
        print(agent(arg).get("model", {}).get("flag_template", ""))
    elif action == "known_models":
        for item in agent(arg).get("model", {}).get("known_models", []):
            print(item)
    elif action == "requires_config":
        print("true" if agent(arg).get("user_model_configuration", {}).get("requires_user_model_config") else "false")
    elif action == "skip_unconfigured":
        print("true" if agent(arg).get("user_model_configuration", {}).get("skip_when_unconfigured") else "false")
    elif action == "skip_message":
        print(agent(arg).get("user_model_configuration", {}).get("skip_message", ""))
    elif action == "config_paths":
        for item in agent(arg).get("user_model_configuration", {}).get("config_paths", []):
            print(os.path.expandvars(os.path.expanduser(item)))
    else:
        raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    main()
