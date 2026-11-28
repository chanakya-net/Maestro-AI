"""The top complexity band is `extreme`; the former name stays readable.

Runs that were already in progress, saved complexity/plan artifacts, and custom
registries may still carry the former band name. They must keep loading,
validating and routing, while everything Maestro writes uses `extreme`.
"""
import json
from pathlib import Path

import pytest

from ai_skills import artifacts, registry as registry_module, routing
from ai_skills.artifacts import GitEvidence, validate_artifact
from ai_skills.registry import BANDS, LEGACY_BAND_ALIASES, canonical_band, load_registry
from ai_skills.routing import RouteRequest, select_route

SOURCE_REGISTRY = Path(__file__).parents[2] / "assets" / "agent-registry.json"
LEGACY = next(iter(LEGACY_BAND_ALIASES))


def test_extreme_is_the_canonical_top_band():
    assert BANDS[-1] == "extreme"
    assert routing.BAND_ORDER[-1] == "extreme"
    assert artifacts.COMPLEXITY_BAND_ORDER[-1] == "extreme"
    assert LEGACY not in BANDS
    assert canonical_band(LEGACY) == "extreme"
    assert canonical_band("complex") == "complex"
    assert canonical_band(None) is None


def test_shipped_registry_and_prompts_use_only_extreme():
    root = Path(__file__).parents[2]
    shipped = [root / "assets" / "agent-registry.json", root / "README.md",
               *sorted((root / "assets").glob("*.md")), *sorted((root / "skills").glob("*/SKILL.md"))]
    for path in shipped:
        assert LEGACY not in path.read_text(encoding="utf-8"), path


def test_custom_registry_with_former_band_name_is_canonicalised(tmp_path):
    raw = json.loads(SOURCE_REGISTRY.read_text(encoding="utf-8"))
    text = json.dumps(raw).replace('"extreme"', f'"{LEGACY}"')
    custom = tmp_path / "agent-registry.json"
    custom.write_text(text, encoding="utf-8")
    (tmp_path / "agent-definitions.json").write_text(
        SOURCE_REGISTRY.with_name("agent-definitions.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert LEGACY in custom.read_text(encoding="utf-8")
    assert load_registry(custom) == load_registry(SOURCE_REGISTRY)


def test_canonical_spelling_wins_when_both_keys_are_present():
    registry = {"model_routing": {"usage_distribution": {"non_complexity_band_target_percent": {
        "extreme": {"codex": 100}, LEGACY: {"claude": 100}}}}}
    registry_module.canonicalize_bands(registry)
    assert registry["model_routing"]["usage_distribution"]["non_complexity_band_target_percent"] == {
        "extreme": {"codex": 100}}


def test_route_accepts_former_band_name_and_reports_extreme():
    registry = load_registry(SOURCE_REGISTRY)
    legacy = select_route(RouteRequest(role="impl", complexity_level=LEGACY), registry, {}).selection
    current = select_route(RouteRequest(role="impl", complexity_level="extreme"), registry, {}).selection
    assert legacy["complexity_level"] == legacy["routing_level"] == "extreme"
    assert (legacy["agent"], legacy["model"]) == (current["agent"], current["model"])


def test_route_cli_accepts_former_band_name(tmp_path, capsys):
    code = routing.main(["--registry-file", str(SOURCE_REGISTRY), "--ledger-file", str(tmp_path / "ledger.json"),
                         "--role", "review", "--complexity-level", LEGACY])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["complexity_level"] == "extreme" and output["routing_level"] == "extreme"


def test_route_cli_still_rejects_unknown_bands(tmp_path):
    with pytest.raises(SystemExit):
        routing.main(["--registry-file", str(SOURCE_REGISTRY), "--ledger-file", str(tmp_path / "ledger.json"),
                      "--role", "impl", "--complexity-level", "ludicrous"])


def _complexity(level, score=5):
    keys = sorted(artifacts.COMPLEXITY_SCORE_KEYS)
    return {"schema_version": 2, "scores": {key: score for key in keys}, "total": score * len(keys),
            "level": level, "rationale": {key: "reason" for key in keys}, "files_inspected": []}


def test_saved_complexity_artifact_with_former_band_name_still_validates():
    assert artifacts.valid_complexity_payload(_complexity("extreme"), allow_legacy=False) is True
    assert artifacts.valid_complexity_payload(_complexity(LEGACY), allow_legacy=False) is True
    assert validate_artifact("complexity", _complexity(LEGACY), GitEvidence(issue="7")).valid is True
    assert artifacts.valid_complexity_payload(_complexity(LEGACY, score=1), allow_legacy=False) is False


def test_new_complexity_scores_compute_extreme():
    assert artifacts.expected_complexity_level({key: 5 for key in artifacts.COMPLEXITY_SCORE_KEYS}) == "extreme"


@pytest.mark.parametrize("level", ["extreme", LEGACY])
def test_saved_plan_with_either_band_name_validates(level):
    payload = {"schema_version": 1, "issue": "7", "role": "plan", "status": "success", "approach": "Do it",
               "complexity_level": level, "complexity_rationale": "Cross-cutting redesign.", "slices": [{"id": 1}]}
    assert artifacts.valid_plan_payload(payload) is True


def _plan(**extra):
    return {"schema_version": 1, "issue": "7", "role": "plan", "status": "success", "approach": "Do it",
            "complexity_level": "medium", "complexity_rationale": "Extends one seam.", "slices": [{"id": 1}], **extra}


def test_plan_without_verification_plan_still_validates():
    assert artifacts.valid_plan_payload(_plan()) is True


@pytest.mark.parametrize("plan", [
    {},
    {"fast_loop_checks": ["pytest tests/unit/test_a.py -q"]},
    {"fast_loop_checks": [], "final_required_checks": ["pytest -q", "ruff check ."],
     "advisory_not_applicable_notes": ["frontend build: no file under web/ is planned to change"]},
])
def test_plan_with_valid_verification_plan_validates(plan):
    assert artifacts.valid_plan_payload(_plan(verification_plan=plan)) is True


@pytest.mark.parametrize("plan", [
    [], "pytest -q", {"final_required_checks": "pytest -q"}, {"final_required_checks": [" "]},
    {"final_required_checks": ["pytest -q", "pytest -q"]}, {"fast_loop_checks": [1]},
    {"not_applicable_checks": ["pytest -q"]},
])
def test_plan_with_invalid_verification_plan_is_rejected(plan):
    assert artifacts.valid_plan_payload(_plan(verification_plan=plan)) is False
