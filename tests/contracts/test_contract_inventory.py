"""A checked-in migration fixture accounts for the legacy compatibility surface."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = Path(__file__).with_name("legacy_inventory.json")


def test_contract_inventory_accounts_for_all_legacy_entrypoints():
    assert INVENTORY.is_file(), "legacy contract inventory has not been frozen"
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    legacy = set(inventory["entrypoints"])
    assert len(legacy) == 24
    assert len([path for path in legacy if path.endswith(".py")]) == 6
    for contract in inventory["entrypoints"].values():
        assert contract["replacement_task"] in range(4, 11)
        for field in ("commands", "flags", "environment", "output", "exit_codes", "json_records"):
            assert field in contract
    assert len(inventory["test_suites"]) == 31
    assert all(item["contract_kind"] in {"behavior", "source", "mixed"}
               for item in inventory["test_suites"].values())


def _require_legacy_checkout():
    if not (ROOT / ".git").exists() or not (ROOT / "assets" / "run-agent.sh").is_file():
        pytest.skip("Source-only legacy comparison requires the Git checkout and legacy scripts; curated sdists intentionally omit them.")


def test_inventory_matches_all_legacy_checkout_entrypoints_and_suites():
    _require_legacy_checkout()
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    tracked = subprocess.check_output(["git", "ls-files", "*.sh", "*.ps1", "*.py"],
                                      cwd=ROOT, text=True).splitlines()
    legacy = {path for path in tracked if not path.startswith(("tests/", "src/"))}
    assert set(inventory["entrypoints"]) == legacy
    suites = {path.name for path in (ROOT / "tests").glob("*.test.sh")}
    assert set(inventory["test_suites"]) == suites


def test_record_fixtures_cover_state_and_pair_migration():
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    records = json.loads(INVENTORY.with_name("legacy_records.json").read_text(encoding="utf-8"))["records"]
    assert {"main-state", "sub-state", "worker-state", "pair-state", "pair-config", "pair-attempt"} <= records.keys()
    assert records["main-state"]["schema_version"] == 4
    assert records["sub-state"]["schema_version"] == 1
    assert records["worker-state"]["schema_version"] == 1
    assert records["pair-state"]["schema_version"] == 2
    assert records["pair-config"]["schema_version"] == 2
    assert {name for contract in inventory["entrypoints"].values()
            for name in contract["json_records"]} <= inventory["record_contracts"].keys()


def test_inventory_preserves_native_windows_parameters_and_aliases():
    """Native entry points accept named parameters independently of GNU wrappers."""
    expected = {
        "assets/worker-watch.ps1": {"-ProcessId", "-Pid", "-DoneFile", "-LogFile", "-TailStateFile", "-TailLines"},
        "assets/run-with-it-stop.ps1": {"-RunRoot", "-PoolStateFile", "-TermWaitSeconds", "-MatchPattern"},
        "assets/run-with-it-watch.ps1": {"-EventsLog", "-PoolStateFile", "-CursorFile", "-WaitSeconds", "-PollSeconds", "-MatchPattern"},
        "assets/run-with-it-dispatch.ps1": {"-Agent", "-Model", "-Detach", "-DetachedChild", "-DryRun", "-ValidateOnly", "-HardLimitSeconds", "-AutoFailStalledRoles"},
        "assets/run-with-it-pool.ps1": {"-ParallelJobs", "-Detach", "-DryRun", "-ValidateOnly", "-MaxSubCoordRecoveryAttempts", "-MaxSubCoordCompactionHandoffs", "-MaxWorkerWaitSeconds"},
        "install.ps1": {"-DryRun", "-List", "-NoColor", "-Only", "-Help"},
        "uninstall.ps1": {"-DryRun", "-List", "-NoColor", "-Only", "-Help"},
    }
    contracts = json.loads(INVENTORY.read_text(encoding="utf-8"))["entrypoints"]
    missing = {path: sorted(flags - set(contracts[path]["flags"]))
               for path, flags in expected.items() if not flags <= set(contracts[path]["flags"])}
    assert missing == {}, f"Native PowerShell public flags/aliases omitted: {missing}"
    for path in expected:
        contract = contracts[path]
        assert set(contract["native_parameters"]) <= set(contract["flags"])
        assert set(contract["native_aliases"]) <= set(contract["flags"])
    assert contracts["assets/worker-watch.ps1"]["native_aliases"] == {"-Pid": "-ProcessId"}


def _legacy_parser(path, monkeypatch):
    """Build argparse definitions without dispatching any helper command."""
    import argparse
    import importlib.util
    spec = importlib.util.spec_from_file_location(Path(path).stem.replace("-", "_"), ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "build_parser"):
        return module.build_parser()
    # The router combines construction and parsing; return the parser before
    # argument validation. Its main() is never invoked and cannot route agents.
    with monkeypatch.context() as patch:
        patch.setattr(argparse.ArgumentParser, "parse_args", lambda parser: parser)
        return module.parse_args()


def _parser_contract(parser):
    import argparse
    commands = set()
    flags = set()
    for action in parser._actions:
        flags.update(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            commands.update(action.choices)
            for child in action.choices.values():
                _, child_flags = _parser_contract(child)
                flags.update(child_flags)
    return commands, flags


def test_inventory_covers_actual_python_parser_commands_and_options(monkeypatch):
    _require_legacy_checkout()
    contracts = json.loads(INVENTORY.read_text(encoding="utf-8"))["entrypoints"]
    missing = {}
    for path, contract in contracts.items():
        if not path.endswith(".py"):
            continue
        commands, flags = _parser_contract(_legacy_parser(path, monkeypatch))
        absent_commands = commands - set(contract["legacy_subcommands"])
        absent_flags = flags - set(contract["flags"])
        if absent_commands or absent_flags:
            missing[path] = {"commands": sorted(absent_commands), "flags": sorted(absent_flags)}
    assert missing == {}, f"Actual argparse contract omitted: {missing}"
