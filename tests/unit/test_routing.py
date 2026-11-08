import copy
import json
from pathlib import Path

import pytest

from ai_skills.routing import RouteRequest, select_route, routing_level
from ai_skills.registry import load_registry
from ai_skills.resources import resource_path
from ai_skills import routing

SOURCE_REGISTRY = Path(__file__).parents[2] / "assets" / "agent-registry.json"
BANDS = ("quite-easy", "easy", "medium", "medium-hard", "complex", "holy-fuck")

def test_role_band_bumps():
    assert routing_level("plan", "easy") == "medium-hard"
    assert routing_level("review", "complex") == "holy-fuck"


@pytest.mark.parametrize("band, model", [
    ("quite-easy", "gemini-3.8-flash-low"),
    ("easy", "gemini-3.8-flash-medium"),
])
def test_forced_agy_uses_only_designated_simple_worker(band, model):
    selection = select_route(
        RouteRequest(role="impl", complexity_level=band, forced_agent="agy"),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    assert (selection["agent"], selection["model"]) == ("agy", model)
    assert [candidate["model"] for candidate in selection["evaluated_candidates"]] == [model]


def test_review_band_bump_uses_easy_flash_pool():
    selection = select_route(
        RouteRequest(role="review", complexity_level="quite-easy", forced_agent="agy"),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    assert (selection["routing_level"], selection["model"]) == ("easy", "gemini-3.8-flash-medium")


def test_plan_band_bump_removes_agy_from_simple_worker_pool():
    with pytest.raises(SystemExit) as exc:
        select_route(
            RouteRequest(role="plan", complexity_level="quite-easy", forced_agent="agy"),
            load_registry(SOURCE_REGISTRY),
            {},
        )
    assert exc.value.code == 2


@pytest.mark.parametrize("band", ["medium", "medium-hard", "complex", "holy-fuck"])
def test_legacy_agy_memberships_do_not_admit_harder_automatic_work(band):
    with pytest.raises(SystemExit) as exc:
        select_route(
            RouteRequest(role="impl", complexity_level=band, forced_agent="agy"),
            load_registry(SOURCE_REGISTRY),
            {},
        )
    assert exc.value.code == 2


@pytest.mark.parametrize("model, band", [
    ("gemini-3.8-flash-low", "quite-easy"),
    ("gemini-3.8-flash-medium", "easy"),
])
def test_flash_worker_pool_follows_catalog_work_band_edits(tmp_path, model, band):
    (tmp_path / "agent-definitions.json").write_text(
        (Path(SOURCE_REGISTRY).parent / "agent-definitions.json").read_text())
    payload = json.loads(SOURCE_REGISTRY.read_text())
    assert model in routing.automatic_policy_model_ids(load_registry(SOURCE_REGISTRY), band)
    payload["model_catalog"][model]["work_bands"] = ["medium"]
    registry_file = tmp_path / "agent-registry.json"
    registry_file.write_text(json.dumps(payload))
    assert model not in routing.automatic_policy_model_ids(load_registry(registry_file), band)


def test_forced_agy_model_keeps_explicit_band_exception():
    selection = select_route(
        RouteRequest(
            role="impl",
            complexity_level="medium",
            forced_agent="agy",
            forced_model="gemini-3.8-flash-low",
        ),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    assert (selection["agent"], selection["model"]) == ("agy", "gemini-3.8-flash-low")


def test_excluded_or_unavailable_flash_falls_back_without_expanding_agy_pool():
    registry = load_registry(SOURCE_REGISTRY)
    excluded = select_route(
        RouteRequest(
            role="impl",
            complexity_level="quite-easy",
            exclude_models=frozenset({"gemini-3.8-flash-low"}),
        ),
        registry,
        {},
    )
    unavailable = select_route(
        RouteRequest(
            role="impl",
            complexity_level="easy",
            availability={"models": {"gemini-3.8-flash-medium"}},
        ),
        registry,
        {},
    )
    assert excluded.agent != "agy"
    assert unavailable.agent != "agy"
    assert excluded.model != "gemini-3.8-flash-low"
    assert unavailable.model != "gemini-3.8-flash-medium"


@pytest.mark.parametrize("restriction", ["unavailable", "excluded"])
def test_restricted_designated_flash_keeps_forced_agy_no_candidate_error(restriction):
    request = RouteRequest(role="impl", complexity_level="easy", forced_agent="agy")
    if restriction == "unavailable":
        request = RouteRequest(
            role="impl",
            complexity_level="easy",
            forced_agent="agy",
            availability={"models": {"gemini-3.8-flash-medium"}},
        )
    else:
        request = RouteRequest(
            role="impl",
            complexity_level="easy",
            forced_agent="agy",
            exclude_models=frozenset({"gemini-3.8-flash-medium"}),
        )
    with pytest.raises(SystemExit) as exc:
        select_route(
            request,
            load_registry(SOURCE_REGISTRY),
            {},
        )
    assert exc.value.code == 2


@pytest.mark.parametrize("band", BANDS)
def test_agy_complexity_scoring_uses_only_flash_medium_at_every_band(band):
    selection = select_route(
        RouteRequest(role="complexity", complexity_level=band, forced_agent="agy"),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    assert (selection["agent"], selection["model"]) == ("agy", "gemini-3.8-flash-medium")
    assert [candidate["model"] for candidate in selection["evaluated_candidates"]] == [
        "gemini-3.8-flash-medium"
    ]


@pytest.mark.parametrize("band", BANDS)
def test_scoring_candidate_expansion_does_not_reintroduce_other_agy_models(band):
    registry = load_registry(SOURCE_REGISTRY)
    candidates = routing.candidate_model_ids(registry, "complexity", band, None, None)
    agy_models = set(registry["agents"]["agy"]["model"]["known_models"])
    agy_candidates = [model for model in candidates if model in agy_models]
    assert agy_candidates == ["gemini-3.8-flash-medium"]


@pytest.mark.parametrize("restriction", ["excluded", "unavailable"])
def test_scoring_medium_restriction_falls_back_or_errors_without_other_agy(restriction):
    registry = load_registry(SOURCE_REGISTRY)
    request = RouteRequest(
        role="complexity",
        complexity_level="medium",
        detected_agents=frozenset({"agy", "codex", "claude"}),
        **({"exclude_models": frozenset({"gemini-3.8-flash-medium"})} if restriction == "excluded" else {
            "availability": {"models": {"gemini-3.8-flash-medium"}}
        }),
    )
    selection = select_route(request, registry, {}).selection
    assert selection["agent"] != "agy"
    assert selection["model"] != "gemini-3.8-flash-medium"
    with pytest.raises(SystemExit) as exc:
        select_route(
            RouteRequest(
                role="complexity",
                complexity_level="medium",
                forced_agent="agy",
                **({"exclude_models": frozenset({"gemini-3.8-flash-medium"})} if restriction == "excluded" else {
                    "availability": {"models": {"gemini-3.8-flash-medium"}}
                }),
            ),
            registry,
            {},
        )
    assert exc.value.code == 2

GOLDEN = json.loads((Path(__file__).parents[1] / "fixtures" / "routing-golden.json").read_text())

@pytest.mark.parametrize("case", GOLDEN, ids=lambda case: str(case["request"]))
def test_golden_routing_policy(case):
    registry=load_registry(SOURCE_REGISTRY)
    request=RouteRequest(**{key:frozenset(value) if isinstance(value,list) else value for key,value in case["request"].items()})
    if "exit_code" in case["expected"]:
        with pytest.raises(SystemExit) as exc: select_route(request,registry,{})
        assert exc.value.code == case["expected"]["exit_code"]
    else:
        selection=select_route(request,registry,{}).selection
        assert {key:selection[key] for key in case["expected"]} == case["expected"]

def test_distribution_and_sticky_reviewer():
    registry=load_registry(SOURCE_REGISTRY)
    ledger=routing.normalize_ledger({})
    for _ in range(120):
        selection=select_route(RouteRequest(role="impl",complexity_level="medium"),registry,ledger).selection
        routing.append_decision(ledger,selection)
    counts=ledger["totals"]["agents"]
    assert counts == {"codex":78,"claude":42}  # Captured legacy policy; AGY has no eligible medium implementation model.
    base=select_route(RouteRequest(role="review",complexity_level="medium"),registry,ledger)
    preferred=base.selection["evaluated_candidates"][-1]["model"]
    sticky=select_route(RouteRequest(role="review",complexity_level="medium",prefer_model=preferred),registry,ledger)
    assert sticky.model == preferred
    assert sticky.reason == "sticky-reviewer-preference"
    fallback=select_route(RouteRequest(role="review",complexity_level="medium",prefer_model=preferred,exclude_models=frozenset([preferred])),registry,ledger)
    assert fallback.model != preferred

def test_cumulative_exclusions_and_read_only_decision():
    registry=load_registry(SOURCE_REGISTRY)
    ledger={}; original=copy.deepcopy(ledger)
    decision=select_route(RouteRequest(role="impl",complexity_level="easy",exclude_models=frozenset(["gpt-6-luna","gpt-6.1-terra"])),registry,ledger)
    assert decision.model not in {"gpt-6-luna","gpt-6.1-terra"}
    assert ledger == original

@pytest.mark.parametrize("payload",["{", "[]"])
def test_malformed_availability_fails_explicitly(tmp_path,payload):
    path=tmp_path / "availability.json"; path.write_text(payload)
    with pytest.raises(SystemExit): routing.availability_from_file(path)

def test_malformed_ledger_is_not_reset(tmp_path):
    path=tmp_path / "ledger.json";path.write_text("{")
    with pytest.raises(SystemExit): routing.read_json_file(path,default={})
    assert path.read_text() == "{"

def test_routing_cli_records_transaction(tmp_path,capsys):
    ledger=tmp_path / "ledger.json"
    assert routing.main(["--registry-file",str(resource_path("agent-registry.json")),"--ledger-file",str(ledger),"--role","impl","--complexity-level","easy","--record"]) == 0
    output=json.loads(capsys.readouterr().out)
    assert output["ledger"]["total_decisions"] == 1
    assert json.loads(ledger.read_text())["decisions"][0]["model"] == output["model"]
    assert not Path(str(ledger)+".lock").exists()

@pytest.mark.parametrize("payload",[{"deny_models":"model"},{"deny_agents":None},{"agents":[]},{"agents":{"codex":{"models":[]}}},{"agents":{"codex":{"status":[]}}}])
def test_malformed_availability_fields_fail_explicitly(tmp_path,payload):
    path=tmp_path/"availability.json";path.write_text(json.dumps(payload))
    with pytest.raises(SystemExit):routing.availability_from_file(path)

@pytest.mark.parametrize("payload",[[],42,"bad"])
def test_nonobject_ledger_rejected(payload):
    with pytest.raises(ValueError):routing.normalize_ledger(payload)


@pytest.mark.parametrize("band, model, effort", [
    ("quite-easy", "claude-haiku-5-5", "high"),
    ("easy", "claude-haiku-5-5", "high"),
    ("medium", "claude-sonnet-5-5", "medium"),
    ("medium-hard", "claude-opus-5-5", "high"),
    ("complex", "claude-opus-5-5", "high"),
    ("holy-fuck", "claude-fable-5-1", "high"),
])
def test_claude_routes_use_current_model_families(band, model, effort):
    decision = select_route(RouteRequest(role="impl", complexity_level=band, forced_agent="claude"), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model, decision.effort) == ("claude", model, effort)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-5-5"])
def test_requested_claude_models_can_be_forced(model):
    decision = select_route(RouteRequest(role="impl", forced_agent="claude", forced_model=model), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model) == ("claude", model)


@pytest.mark.parametrize("model", [
    "claude-haiku-4-5", "claude-haiku-4.5", "claude-sonnet-4-6", "claude-sonnet-4.6",
    "claude-sonnet-4-6[1m]", "claude-opus-4-6", "claude-opus-5", "claude-sonnet-5",
    "claude-opus-5-high", "claude-opus-5-xhigh", "claude-sonnet-4.6-thinking", "claude-opus-4.6-thinking",
])
def test_retired_claude_models_cannot_be_routed(model):
    with pytest.raises(SystemExit):
        select_route(RouteRequest(role="impl", forced_model=model), load_registry(SOURCE_REGISTRY), {})


@pytest.mark.parametrize("band, model, effort", [
    ("quite-easy", "gpt-6-luna", "high"),
    ("easy", "gpt-6-luna", "high"),
    ("medium", "gpt-6.1-terra", "high"),
    ("medium-hard", "gpt-6.1-sol", "high"),
    ("complex", "gpt-6.1-sol", "high"),
    ("holy-fuck", "gpt-6-astra", "high"),
])
def test_codex_routes_use_requested_model_families(band, model, effort):
    decision = select_route(RouteRequest(role="impl", complexity_level=band, forced_agent="codex"), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model, decision.effort) == ("codex", model, effort)


@pytest.mark.parametrize("band, model, effort", [
    ("quite-easy", "gpt-6-luna", "high"), ("easy", "gpt-6-luna", "high"), ("medium", "gpt-6.1-terra", "high"),
    ("medium-hard", "gpt-6.1-sol", "high"), ("complex", "gpt-6.1-sol", "high"), ("holy-fuck", "gpt-6.1-sol", "high"),
])
def test_codex_complexity_scoring_has_a_supported_model_at_every_band(band, model, effort):
    decision = select_route(RouteRequest(role="complexity", complexity_level=band, forced_agent="codex"), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model, decision.effort) == ("codex", model, effort)


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-astra", "gpt-6.1-terra", "gpt-6-luna"])
def test_requested_codex_models_can_be_forced(model):
    decision = select_route(RouteRequest(role="impl", forced_agent="codex", forced_model=model), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model) == ("codex", model)


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.4-mini", "gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.5"])
def test_retired_codex_models_cannot_be_routed_on_codex(model):
    with pytest.raises(SystemExit):
        select_route(RouteRequest(role="impl", forced_agent="codex", forced_model=model), load_registry(SOURCE_REGISTRY), {})


DIRECT_HARNESSES = ("claude", "codex")
NON_PLANNING_ROLES = ("impl", "review", "modify", "complexity", "artifact-recovery", "merge-recovery")
DIRECT_MODELS = [
    (agent, model)
    for model, entry in json.loads(SOURCE_REGISTRY.read_text())["model_catalog"].items()
    for agent in entry.get("agents", [])
    if agent in DIRECT_HARNESSES
]


@pytest.mark.parametrize("band", BANDS)
@pytest.mark.parametrize("role", NON_PLANNING_ROLES)
@pytest.mark.parametrize("agent, model", DIRECT_MODELS)
def test_direct_non_planning_effort_is_high_or_lower_configured(agent, model, role, band):
    decision = select_route(
        RouteRequest(role=role, complexity_level=band, forced_agent=agent, forced_model=model),
        load_registry(SOURCE_REGISTRY), {})
    assert decision.model == model
    if agent == "codex":
        assert decision.effort == "high"
    else:
        assert decision.effort in {"low", "medium", "high"}


@pytest.mark.parametrize("band", BANDS)
@pytest.mark.parametrize("agent, model", DIRECT_MODELS)
def test_direct_planning_effort_is_xhigh_for_every_band(agent, model, band):
    decision = select_route(
        RouteRequest(role="plan", complexity_level=band, forced_agent=agent, forced_model=model),
        load_registry(SOURCE_REGISTRY), {})
    assert decision.model == model
    assert decision.effort == "xhigh"


@pytest.mark.parametrize("band", BANDS)
@pytest.mark.parametrize("agent, model", DIRECT_MODELS)
def test_high_difficulty_never_grants_planning_effort_to_ordinary_roles(agent, model, band):
    decision = select_route(
        RouteRequest(role="review", complexity_level=band, forced_agent=agent, forced_model=model),
        load_registry(SOURCE_REGISTRY), {})
    assert decision.effort != "xhigh"
    assert decision.effort not in {"max", "maximum"}


@pytest.mark.parametrize("band, effort", [("quite-easy", "low"), ("easy", "medium"), ("medium", "medium")])
def test_claude_lower_configured_efforts_survive_non_planning_routes(band, effort):
    decision = select_route(
        RouteRequest(role="impl", complexity_level=band, forced_agent="claude", forced_model="claude-sonnet-5-5"),
        load_registry(SOURCE_REGISTRY), {})
    assert decision.effort == effort


def test_claude_automatic_planning_defaults_to_xhigh_over_lower_configured_effort():
    decision = select_route(
        RouteRequest(role="plan", complexity_level="quite-easy", forced_agent="claude", forced_model="claude-sonnet-5-5"),
        load_registry(SOURCE_REGISTRY), {})
    assert (decision.model, decision.effort) == ("claude-sonnet-5-5", "xhigh")


def test_planning_effort_uses_original_plan_role_not_routing_level():
    decision = select_route(
        RouteRequest(role="plan", complexity_level="quite-easy", forced_agent="codex"),
        load_registry(SOURCE_REGISTRY), {})
    assert (decision.routing_level, decision.effort) == ("medium", "xhigh")


@pytest.mark.parametrize("agent, model", [("claude", "claude-sonnet-5-5"), ("codex", "gpt-6.1-sol")])
@pytest.mark.parametrize("role", ["impl", "plan"])
@pytest.mark.parametrize("setting", ["model_default", "band_override"])
def test_direct_routes_reject_unknown_configured_effort(agent, model, role, setting):
    registry = load_registry(SOURCE_REGISTRY)
    level = routing.routing_level(role, "medium")
    if setting == "model_default":
        registry["model_catalog"][model]["reasoning_effort"] = "bogus"
        registry["model_routing"]["effort_by_model_and_band"].setdefault(model, {}).pop(level, None)
    else:
        registry["model_routing"]["effort_by_model_and_band"].setdefault(model, {})[level] = "bogus"

    with pytest.raises(ValueError, match="unknown effort: bogus"):
        select_route(
            RouteRequest(role=role, complexity_level="medium", forced_agent=agent, forced_model=model),
            registry,
            {},
        )
