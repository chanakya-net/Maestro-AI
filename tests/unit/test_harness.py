import json
import os
from pathlib import Path

import pytest

from ai_skills import harness, routing
from ai_skills.harness import HarnessError
from ai_skills.registry import load_registry
from ai_skills.routing import RouteRequest, select_route

SOURCE_REGISTRY = Path(__file__).parents[2] / "assets" / "agent-registry.json"


@pytest.fixture
def registry():
    return load_registry(SOURCE_REGISTRY)


def fake_path(tmp_path, *commands):
    """A PATH holding only the given fake CLI executables."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for command in commands:
        target = bin_dir / (command + (".cmd" if os.name == "nt" else ""))
        target.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
        target.chmod(0o755)
    return {"PATH": str(bin_dir), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
            "RUN_AGENT_BOOTSTRAP_PATH": "0", "PATHEXT": ".CMD;.EXE"}


def plan_for(shares, source="user"):
    return harness.build_plan(shares, list(shares), source)


# ------------------------------------------------------------------ shares


def test_round_to_100_is_exact_and_stable():
    assert harness.round_to_100({"a": 1, "b": 1, "c": 1}) == {"a": 34, "b": 33, "c": 33}
    assert sum(harness.round_to_100({"a": 38, "b": 37, "c": 25, "d": 10}).values()) == 100
    assert harness.round_to_100({"a": 0, "b": 0}) == {"a": 0, "b": 0}


def test_default_shares_rescale_registry_defaults_for_installed_harnesses(registry):
    assert harness.default_shares(registry, ["codex", "claude", "agy"]) == {"agy": 38, "codex": 37, "claude": 25}
    four = harness.default_shares(registry, ["codex", "claude", "agy", "github-copilot"])
    assert sum(four.values()) == 100 and four["github-copilot"] == 9
    assert harness.default_shares(registry, ["claude"]) == {"claude": 100}
    assert harness.default_shares(registry, []) == {}


def test_default_shares_split_evenly_without_configured_defaults(registry):
    assert harness.default_shares(registry, ["opencode"]) == {"opencode": 100}


def test_parse_shares_accepts_aliases_and_percent_signs(registry):
    assert harness.parse_shares(registry, "codex=50%, copilot=20 ,claude-code=30") == {
        "codex": 50, "github-copilot": 20, "claude": 30}


@pytest.mark.parametrize("text", ["", "codex", "codex=abc", "codex=-1", "codex=1.5", "codex=50,codex=50", "codex=²"])
def test_parse_shares_rejects_malformed_input(registry, text):
    with pytest.raises(HarnessError):
        harness.parse_shares(registry, text)


def test_validate_shares_fills_unmentioned_installed_harnesses_with_zero():
    assert harness.validate_shares({"codex": 60, "claude": 40}, ["codex", "claude", "agy"]) == {
        "codex": 60, "claude": 40, "agy": 0}


@pytest.mark.parametrize("shares, message", [
    ({"codex": 60, "claude": 30}, "add up to 100"),
    ({"codex": 60, "github-copilot": 40}, "not installed"),
    ({"codex": 101}, "0 to 100"),
    ({"codex": True}, "0 to 100"),
    ({"codex": 0, "claude": 0}, "add up to 100"),
])
def test_validate_shares_rejects_invalid_splits(shares, message):
    with pytest.raises(HarnessError, match=message):
        harness.validate_shares(shares, ["codex", "claude"])


def test_validate_shares_requires_an_installed_harness():
    with pytest.raises(HarnessError, match="no supported coding harness"):
        harness.validate_shares({}, [])


# ------------------------------------------------------------------ detection


def test_detect_lists_installed_routable_harnesses_with_defaults(tmp_path, registry):
    report = harness.detect(registry, fake_path(tmp_path, "codex", "claude", "copilot"), tmp_path)
    assert report["installed"] == ["codex", "claude", "github-copilot"]
    assert sum(report["default_shares"].values()) == 100
    rows = {row["agent"]: row for row in report["harnesses"]}
    assert rows["agy"]["status"] == "missing" and rows["agy"]["usable"] is False
    assert rows["github-copilot"]["work_bands"] == ["quite-easy", "easy"]
    assert "github-copilot" in report["notes"] and "codex" not in report["notes"]


def test_detect_skips_installed_harness_without_routable_models(tmp_path, registry):
    env = fake_path(tmp_path, "opencode", "codex")
    (tmp_path / ".opencode.json").write_text("{}", encoding="utf-8")
    report = harness.detect(registry, env, tmp_path)
    rows = {row["agent"]: row for row in report["harnesses"]}
    assert rows["opencode"]["status"] == "detected"
    assert rows["opencode"]["usable"] is False
    assert report["installed"] == ["codex"]
    assert report["default_shares"] == {"codex": 100}


def test_detect_reports_nothing_installed(tmp_path, registry):
    report = harness.detect(registry, fake_path(tmp_path), tmp_path)
    assert report["installed"] == [] and report["default_shares"] == {}


def test_copilot_is_no_longer_disabled(registry):
    from ai_skills.runner import _agent
    assert _agent(registry, "copilot")[0] == "github-copilot"
    assert routing.registry_availability(registry)["agents"] == set()


def test_routing_disabled_flag_still_blocks_any_harness(registry):
    from ai_skills.runner import _agent
    registry["agents"]["github-copilot"]["routing_disabled"] = True
    with pytest.raises(ValueError, match="disabled"):
        _agent(registry, "copilot")


# ------------------------------------------------------------------ plan file


def test_plan_round_trip_and_refuses_silent_overwrite(tmp_path):
    plan = plan_for({"codex": 70, "claude": 30})
    path = harness.save_plan(tmp_path, plan)
    assert path == tmp_path.absolute() / ".run-with-it" / "harness-plan.json"
    assert harness.load_plan(path.parent) == plan
    with pytest.raises(HarnessError, match="already exists"):
        harness.save_plan(tmp_path, plan_for({"codex": 100}))
    harness.save_plan(tmp_path, plan_for({"codex": 100}), force=True)
    assert harness.load_plan(path.parent)["shares"] == {"codex": 100}


def test_load_plan_absent_is_none(tmp_path):
    assert harness.load_plan(tmp_path) is None


@pytest.mark.parametrize("plan", [
    [],
    {"schema_version": 2},
    {"schema_version": 1, "detected": ["codex"], "shares": {"codex": 90}, "source": "user"},
    {"schema_version": 1, "detected": ["codex"], "shares": {"claude": 100}, "source": "user"},
    {"schema_version": 1, "detected": ["codex"], "shares": {"codex": 100}, "source": "guess"},
])
def test_load_plan_rejects_invalid_files(tmp_path, plan):
    (tmp_path / "harness-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(HarnessError):
        harness.load_plan(tmp_path)


def test_load_plan_rejects_malformed_json(tmp_path):
    (tmp_path / "harness-plan.json").write_text("{", encoding="utf-8")
    with pytest.raises(HarnessError, match="invalid JSON"):
        harness.load_plan(tmp_path)


# ------------------------------------------------------------------ routing policy


def test_apply_plan_sets_global_target_and_reweights_target_tables(registry):
    applied = harness.apply_plan(registry, plan_for({"codex": 50, "claude": 50}))
    distribution = applied["model_routing"]["usage_distribution"]
    assert distribution["default_target_percent"] == {"codex": 50, "claude": 50}
    review = distribution["role_target_percent"]["review"]
    impl = distribution["role_target_percent"]["impl"]
    assert set(review) == {"codex", "claude"} and sum(review.values()) == 100
    assert review["claude"] > review["codex"]
    assert impl["codex"] > impl["claude"]
    for band in distribution["non_complexity_band_target_percent"].values():
        assert set(band) == {"codex", "claude"} and sum(band.values()) == 100
    assert registry["model_routing"]["usage_distribution"]["default_target_percent"] == {"agy": 38, "codex": 37, "claude": 25}


def test_apply_plan_keeps_hard_zero_rules(registry):
    applied = harness.apply_plan(registry, plan_for({"agy": 50, "claude": 50}))
    plan_targets = applied["model_routing"]["usage_distribution"]["role_target_percent"]["plan"]
    assert plan_targets == {"claude": 100, "agy": 0}


def test_apply_plan_gives_harness_missing_from_tables_its_user_share(registry):
    applied = harness.apply_plan(registry, plan_for({"codex": 70, "github-copilot": 30}))
    easy = applied["model_routing"]["usage_distribution"]["non_complexity_band_target_percent"]["easy"]
    # codex: 33 * 70 / 37 ≈ 62.4 ; copilot (absent from the table): 30  → rescaled
    assert easy == {"codex": 68, "github-copilot": 32}


def test_apply_plan_rejects_unknown_harness(registry):
    with pytest.raises(HarnessError, match="unknown harness"):
        harness.apply_plan(registry, plan_for({"nope": 100}))


def test_zero_share_harness_is_never_routed(registry):
    plan = plan_for({"codex": 0, "claude": 100})
    for role, band in (("impl", "easy"), ("review", "medium"), ("complexity", "medium"), ("modify", "complex")):
        decision = select_route(RouteRequest(role=role, complexity_level=band, harness_plan=plan), registry, {})
        assert decision.agent == "claude"


def test_explicit_detected_agents_cannot_widen_the_plan(registry):
    plan = plan_for({"codex": 100})
    request = RouteRequest(role="impl", complexity_level="easy", detected_agents=frozenset({"claude", "agy"}),
                           harness_plan=plan)
    with pytest.raises(SystemExit):
        select_route(request, registry, {})


def test_copilot_routes_for_easy_work_when_in_the_plan(registry):
    plan = plan_for({"github-copilot": 100})
    decision = select_route(RouteRequest(role="impl", complexity_level="easy", harness_plan=plan), registry, {})
    assert (decision.agent, decision.model) == ("github-copilot", "gpt-5.4")


def test_usage_follows_user_split_over_many_routes(registry):
    plan = plan_for({"codex": 80, "claude": 20})
    ledger = routing.normalize_ledger({})
    for _ in range(100):
        selection = select_route(RouteRequest(role="impl", complexity_level="medium", harness_plan=plan),
                                 registry, ledger).selection
        routing.append_decision(ledger, selection)
    counts = ledger["totals"]["agents"]
    assert set(counts) == {"codex", "claude"}
    assert 70 <= counts["codex"] <= 90


def test_route_cli_reads_plan_beside_ledger(tmp_path, capsys):
    run_dir = tmp_path / ".run-with-it"
    harness.save_plan(tmp_path, plan_for({"claude": 100}))
    code = routing.main(["--registry-file", str(SOURCE_REGISTRY), "--ledger-file", str(run_dir / "usage-ledger.json"),
                         "--role", "impl", "--complexity-level", "medium", "--record"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["agent"] == "claude"
    assert output["harness_plan"] == {"source": "user", "shares": {"claude": 100}}


def test_route_cli_without_plan_keeps_legacy_defaults(tmp_path, capsys):
    code = routing.main(["--registry-file", str(SOURCE_REGISTRY), "--ledger-file", str(tmp_path / "ledger.json"),
                         "--role", "impl", "--complexity-level", "medium"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["agent"] in {"codex", "agy", "claude"} and "harness_plan" not in output


def test_route_cli_fails_closed_on_invalid_plan(tmp_path):
    run_dir = tmp_path / ".run-with-it"
    run_dir.mkdir()
    (run_dir / "harness-plan.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        routing.main(["--registry-file", str(SOURCE_REGISTRY), "--ledger-file", str(run_dir / "usage-ledger.json"),
                      "--role", "impl", "--complexity-level", "medium"])
    assert exc.value.code == 2


# ------------------------------------------------------------------ CLI


def test_cli_plan_defaults_then_show(tmp_path, monkeypatch, capsys):
    for key, value in fake_path(tmp_path, "codex", "claude").items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AGENT_REGISTRY_FILE", raising=False)
    assert harness.main(["plan", "--run-root", str(tmp_path), "--defaults", "--registry-file", str(SOURCE_REGISTRY)]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["source"] == "default" and saved["shares"] == {"codex": 60, "claude": 40}
    assert harness.main(["show", "--run-root", str(tmp_path), "--registry-file", str(SOURCE_REGISTRY)]) == 0
    assert "Codex 60%" in json.loads(capsys.readouterr().out)["summary"]


def test_cli_plan_rejects_bad_split_and_harness_not_installed(tmp_path, monkeypatch, capsys):
    for key, value in fake_path(tmp_path, "codex").items():
        monkeypatch.setenv(key, value)
    args = ["plan", "--run-root", str(tmp_path), "--registry-file", str(SOURCE_REGISTRY), "--shares"]
    assert harness.main([*args, "codex=90"]) == 2
    assert harness.main([*args, "codex=50,claude=50"]) == 2
    assert "not installed" in capsys.readouterr().err
    assert not harness.plan_path(tmp_path).exists()


def test_cli_show_without_plan_fails(tmp_path, capsys):
    assert harness.main(["show", "--run-root", str(tmp_path), "--registry-file", str(SOURCE_REGISTRY)]) == 1


def test_build_plan_rejects_partial_split_when_full_harness_installed_and_warns_for_agy_only(registry):
    with pytest.raises(HarnessError, match="cannot route all complexity bands"):
        harness.build_plan({"github-copilot": 100, "codex": 0}, ["github-copilot", "codex"], "user", registry=registry)

    # Explicit opt-in succeeds with warnings
    partial = harness.build_plan(
        {"github-copilot": 100, "codex": 0}, ["github-copilot", "codex"], "user",
        registry=registry, allow_partial=True,
    )
    assert partial["warnings"] and "medium" in partial["warnings"][0]

    # AGY alone covers all 6 work bands, so it succeeds with a plan-phase warning
    agy_only = harness.build_plan({"agy": 100, "codex": 0}, ["agy", "codex"], "user", registry=registry)
    assert agy_only["warnings"] and "plan phase" in agy_only["warnings"][0]
