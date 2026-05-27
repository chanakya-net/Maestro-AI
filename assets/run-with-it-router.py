#!/usr/bin/env python3
"""Deterministic run-with-it model router.

The router keeps subscription usage balanced across supported CLI tools while
still respecting complexity bands, role-specific preferences, and explicit
operator overrides.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


BAND_ORDER = ["quite-easy", "easy", "medium", "medium-hard", "complex", "holy-fuck"]
REVIEW_BUMP = {
    "quite-easy": "easy",
    "easy": "medium",
    "medium": "medium-hard",
    "medium-hard": "complex",
    "complex": "holy-fuck",
    "holy-fuck": "holy-fuck",
}
DEFAULT_AGENTS = ["codex", "agy", "github-copilot", "claude"]
GLOBAL_DEBT_WEIGHT = 1.5


def fail(message: str) -> None:
    print(f"run-with-it-router: {message}", file=sys.stderr)
    raise SystemExit(2)


def read_json_file(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        fail(f"missing JSON file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        if default is not None:
            return default
        fail(f"invalid JSON in {path}: {exc}")


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


class DirectoryLock:
    def __init__(self, path: Path, timeout_seconds: float = 10.0) -> None:
        self.path = Path(f"{path}.lock")
        self.timeout_seconds = timeout_seconds

    def __enter__(self) -> "DirectoryLock":
        started = time.monotonic()
        while True:
            try:
                self.path.mkdir(parents=True)
                return self
            except FileExistsError:
                if time.monotonic() - started > self.timeout_seconds:
                    fail(f"timed out waiting for ledger lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        try:
            self.path.rmdir()
        except FileNotFoundError:
            pass


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_ledger(ledger: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ledger, dict):
        ledger = {}
    ledger.setdefault("schema_version", 1)
    ledger.setdefault("decisions", [])
    totals = ledger.setdefault("totals", {})
    agents = totals.setdefault("agents", {})
    if not agents and ledger.get("decisions"):
        for decision in ledger["decisions"]:
            agent = decision.get("agent")
            if agent:
                agents[agent] = agents.get(agent, 0) + 1
    return ledger


def current_agent_counts(ledger: dict[str, Any]) -> dict[str, int]:
    raw_counts = ledger.get("totals", {}).get("agents", {})
    return {str(agent): int(count) for agent, count in raw_counts.items()}


def role_agent_counts(ledger: dict[str, Any], role: str) -> dict[str, int]:
    raw_counts = ledger.get("totals", {}).get("roles", {}).get(role, {}).get("agents", {})
    return {str(agent): int(count) for agent, count in raw_counts.items()}


def score_to_level(registry: dict[str, Any], score: int) -> str:
    for row in registry.get("model_routing", {}).get("score_to_weight", []):
        if int(row["score_min"]) <= score <= int(row["score_max"]):
            return str(row["label"])
    if score > 40:
        return "holy-fuck"
    fail(f"complexity score does not map to a routing band: {score}")


def weight_range_for_level(registry: dict[str, Any], level: str) -> tuple[int, int]:
    for row in registry.get("model_routing", {}).get("score_to_weight", []):
        if row.get("label") == level:
            return int(row["weight_min"]), int(row["weight_max"])
    fail(f"unknown complexity level: {level}")


def min_band_allows(model_entry: dict[str, Any], level: str) -> bool:
    min_band = model_entry.get("min_band")
    if not min_band:
        return True
    if min_band not in BAND_ORDER or level not in BAND_ORDER:
        return True
    return BAND_ORDER.index(level) >= BAND_ORDER.index(min_band)


def routing_level(role: str, base_level: str) -> str:
    if role == "review":
        return REVIEW_BUMP.get(base_level, base_level)
    return base_level


def target_policy(registry: dict[str, Any], role: str, level: str) -> dict[str, int]:
    distribution = registry.get("model_routing", {}).get("usage_distribution", {})
    default_target = distribution.get("default_target_percent", {})
    role_band = distribution.get("role_band_target_percent", {})
    role_targets = distribution.get("role_target_percent", {})
    policy = role_band.get(role, {}).get(level) or role_targets.get(role) or default_target
    return {str(agent): int(percent) for agent, percent in policy.items()}


def role_agent_preference(registry: dict[str, Any], role: str) -> list[str]:
    distribution = registry.get("model_routing", {}).get("usage_distribution", {})
    preferences = distribution.get("role_agent_preference", {})
    value = preferences.get(role) or preferences.get("default") or DEFAULT_AGENTS
    return [str(agent) for agent in value]


def compatible_agents_for_model(
    registry: dict[str, Any],
    model_id: str,
    detected_agents: set[str],
    allowlist: set[str],
    denylist: set[str],
    forced_agent: str | None,
) -> list[str]:
    agents = registry.get("agents", {})
    model_entry = registry.get("model_catalog", {}).get(model_id, {})
    provider = model_entry.get("provider")
    candidates: list[str] = []

    for agent_id, agent in agents.items():
        if forced_agent and agent_id != forced_agent:
            continue
        if agent_id not in detected_agents:
            continue
        if allowlist and agent_id not in allowlist:
            continue
        if agent_id in denylist:
            continue
        if model_id not in agent.get("model", {}).get("known_models", []):
            continue
        if provider == "google" and agent_id != "agy":
            continue
        candidates.append(agent_id)

    return candidates


def candidate_model_ids(
    registry: dict[str, Any],
    role: str,
    level: str,
    forced_model: str | None,
    exclude_model: str | None,
) -> list[str]:
    catalog = registry.get("model_catalog", {})
    if forced_model:
        if forced_model not in catalog:
            fail(f"forced model is not in model_catalog: {forced_model}")
        return [forced_model]

    routing = registry.get("model_routing", {})
    if role == "complexity":
        weight_min, weight_max = 1, 6
    else:
        weight_min, weight_max = weight_range_for_level(registry, level)

    candidates: list[str] = []
    for model_id, entry in catalog.items():
        if model_id == exclude_model:
            continue
        if role == "complexity" and entry.get("exclude_from_complexity") is True:
            continue
        if not min_band_allows(entry, level):
            continue
        weight = int(entry.get("complexity_weight", 99))
        if weight_min <= weight <= weight_max:
            candidates.append(model_id)

    for model_id in routing.get("band_required_models", {}).get(level, []):
        if model_id != exclude_model and model_id in catalog and min_band_allows(catalog[model_id], level):
            if model_id not in candidates:
                candidates.append(model_id)

    for expansion in range(1, 4):
        if candidates:
            break
        expanded_max = weight_max + expansion
        for model_id, entry in catalog.items():
            if model_id == exclude_model:
                continue
            if role == "complexity" and entry.get("exclude_from_complexity") is True:
                continue
            if not min_band_allows(entry, level):
                continue
            weight = int(entry.get("complexity_weight", 99))
            if weight_min <= weight <= expanded_max:
                candidates.append(model_id)

    return candidates


def candidate_pairs(
    registry: dict[str, Any],
    role: str,
    level: str,
    detected_agents: set[str],
    allowlist: set[str],
    denylist: set[str],
    forced_agent: str | None,
    forced_model: str | None,
    exclude_model: str | None,
) -> list[dict[str, Any]]:
    catalog = registry.get("model_catalog", {})
    model_ids = candidate_model_ids(registry, role, level, forced_model, exclude_model)
    pairs: list[dict[str, Any]] = []
    for model_id in model_ids:
        for agent_id in compatible_agents_for_model(
            registry,
            model_id,
            detected_agents,
            allowlist,
            denylist,
            forced_agent,
        ):
            entry = catalog[model_id]
            pairs.append(
                {
                    "agent": agent_id,
                    "model": model_id,
                    "provider": entry.get("provider", "unknown"),
                    "ability": entry.get("ability", "unknown"),
                    "complexity_weight": int(entry.get("complexity_weight", 99)),
                    "context_window": int(entry.get("context_window", 0)),
                }
            )
    return pairs


def select_pair(
    registry: dict[str, Any],
    ledger: dict[str, Any],
    role: str,
    base_level: str,
    detected_agents: set[str],
    allowlist: set[str],
    denylist: set[str],
    forced_agent: str | None,
    forced_model: str | None,
    exclude_model: str | None,
) -> dict[str, Any]:
    level = routing_level(role, base_level)
    if forced_agent and forced_model:
        reason = "forced-agent-and-model"
    elif forced_agent:
        reason = "forced-agent"
    elif forced_model:
        reason = "forced-model"
    else:
        reason = "usage-share-debt"

    pairs = candidate_pairs(
        registry,
        role,
        level,
        detected_agents,
        allowlist,
        denylist,
        forced_agent,
        forced_model,
        exclude_model,
    )
    if not pairs:
        fail(
            "no compatible routing candidates "
            f"role={role} level={level} detected={sorted(detected_agents)} "
            f"allowlist={sorted(allowlist)} denylist={sorted(denylist)}"
        )

    distribution = registry.get("model_routing", {}).get("usage_distribution", {})
    global_policy = {
        str(agent): int(percent)
        for agent, percent in distribution.get("default_target_percent", {}).items()
    }
    policy = target_policy(registry, role, level)
    preferences = role_agent_preference(registry, role)
    preference_rank = {agent: index for index, agent in enumerate(preferences)}
    counts = current_agent_counts(ledger)
    role_counts = role_agent_counts(ledger, role)
    total = sum(counts.values())
    role_total = sum(role_counts.values())
    weight_min, weight_max = (1, 6) if role == "complexity" else weight_range_for_level(registry, level)
    weight_center = (weight_min + weight_max) / 2.0

    def sort_key(pair: dict[str, Any]) -> tuple[Any, ...]:
        agent = pair["agent"]
        role_target = policy.get(agent, 0)
        global_target = global_policy.get(agent, role_target)
        if role_target <= 0 and not forced_agent:
            target_penalty = 1000
        else:
            target_penalty = 0
        current = (counts.get(agent, 0) / total * 100.0) if total else 0.0
        role_current = (role_counts.get(agent, 0) / role_total * 100.0) if role_total else 0.0
        projected = ((counts.get(agent, 0) + 1) / (total + 1) * 100.0)
        role_projected = ((role_counts.get(agent, 0) + 1) / (role_total + 1) * 100.0)
        global_debt = global_target - current
        role_debt = role_target - role_current
        combined_debt = (global_debt * GLOBAL_DEBT_WEIGHT) + role_debt
        projected_error = abs(projected - global_target) + abs(role_projected - role_target)
        return (
            target_penalty,
            -combined_debt,
            projected_error,
            -global_debt,
            -role_debt,
            -global_target,
            -role_target,
            preference_rank.get(agent, 999),
            abs(pair["complexity_weight"] - weight_center),
            -pair["context_window"],
            pair["model"],
            pair["agent"],
        )

    selected = sorted(pairs, key=sort_key)[0]
    agent = selected["agent"]
    current_percent = (counts.get(agent, 0) / total * 100.0) if total else 0.0
    selected.update(
        {
            "role": role,
            "complexity_level": base_level,
            "routing_level": level,
            "selection_reason": reason,
            "target_percent": policy.get(agent, 0),
            "global_target_percent": global_policy.get(agent, policy.get(agent, 0)),
            "current_percent": round(current_percent, 2),
        }
    )
    selected["evaluated_candidates"] = [
        {
            "agent": pair["agent"],
            "model": pair["model"],
            "target_percent": policy.get(pair["agent"], 0),
            "complexity_weight": pair["complexity_weight"],
        }
        for pair in sorted(pairs, key=sort_key)[:8]
    ]
    return selected


def append_decision(ledger: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    decision = {
        "selected_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "role": selection["role"],
        "complexity_level": selection["complexity_level"],
        "routing_level": selection["routing_level"],
        "agent": selection["agent"],
        "model": selection["model"],
        "selection_reason": selection["selection_reason"],
    }
    ledger.setdefault("decisions", []).append(decision)
    totals = ledger.setdefault("totals", {})
    agents = totals.setdefault("agents", {})
    agents[selection["agent"]] = int(agents.get(selection["agent"], 0)) + 1
    roles = totals.setdefault("roles", {})
    role_totals = roles.setdefault(selection["role"], {}).setdefault("agents", {})
    role_totals[selection["agent"]] = int(role_totals.get(selection["agent"], 0)) + 1
    return ledger


def build_output(
    registry: dict[str, Any],
    ledger: dict[str, Any],
    ledger_file: Path,
    selection: dict[str, Any],
    updated: bool,
) -> dict[str, Any]:
    distribution = registry.get("model_routing", {}).get("usage_distribution", {})
    counts = current_agent_counts(ledger)
    total = sum(counts.values())
    agent = selection["agent"]
    output = {
        "schema_version": 1,
        "agent": agent,
        "model": selection["model"],
        "role": selection["role"],
        "complexity_level": selection["complexity_level"],
        "routing_level": selection["routing_level"],
        "selection_reason": selection["selection_reason"],
        "target_percent": selection["target_percent"],
        "global_target_percent": selection.get("global_target_percent", selection["target_percent"]),
        "current_percent": selection["current_percent"],
        "policy": {
            "default_target_percent": distribution.get("default_target_percent", {}),
            "role_target_percent": distribution.get("role_target_percent", {}).get(selection["role"], {}),
        },
        "ledger": {
            "path": str(ledger_file),
            "updated": updated,
            "total_decisions": total,
            "agent_counts": counts,
            "selected_agent_count": counts.get(agent, 0),
        },
        "evaluated_candidates": selection.get("evaluated_candidates", []),
    }
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a run-with-it worker agent/model.")
    parser.add_argument("--registry-file", required=True)
    parser.add_argument("--ledger-file", required=True)
    parser.add_argument("--role", required=True, choices=["complexity", "impl", "review", "modify", "merge-recovery"])
    parser.add_argument("--complexity-level", choices=BAND_ORDER)
    parser.add_argument("--complexity-score", type=int)
    parser.add_argument("--detected-agents", default=",".join(DEFAULT_AGENTS))
    parser.add_argument("--allowlist", default="")
    parser.add_argument("--denylist", default="")
    parser.add_argument("--forced-agent", default="")
    parser.add_argument("--forced-model", default="")
    parser.add_argument("--exclude-model", default="")
    parser.add_argument("--record", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry_file = Path(args.registry_file)
    ledger_file = Path(args.ledger_file)
    registry = read_json_file(registry_file)

    if args.complexity_level:
        base_level = args.complexity_level
    elif args.complexity_score is not None:
        base_level = score_to_level(registry, args.complexity_score)
    else:
        fail("pass --complexity-level or --complexity-score")

    detected_agents = set(split_csv(args.detected_agents))
    allowlist = set(split_csv(args.allowlist))
    denylist = set(split_csv(args.denylist))
    forced_agent = args.forced_agent or None
    forced_model = args.forced_model or None
    exclude_model = args.exclude_model or None

    if args.record:
        with DirectoryLock(ledger_file):
            ledger = normalize_ledger(read_json_file(ledger_file, default={}))
            selection = select_pair(
                registry,
                ledger,
                args.role,
                base_level,
                detected_agents,
                allowlist,
                denylist,
                forced_agent,
                forced_model,
                exclude_model,
            )
            ledger = append_decision(ledger, selection)
            write_json_atomic(ledger_file, ledger)
            output = build_output(registry, ledger, ledger_file, selection, updated=True)
    else:
        ledger = normalize_ledger(read_json_file(ledger_file, default={}))
        selection = select_pair(
            registry,
            ledger,
            args.role,
            base_level,
            detected_agents,
            allowlist,
            denylist,
            forced_agent,
            forced_model,
            exclude_model,
        )
        output = build_output(registry, ledger, ledger_file, selection, updated=False)

    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
