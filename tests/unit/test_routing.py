import copy
import json
import sys
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


AGY_WORK_MATRIX = {
    "quite-easy": {"gemini-3.8-flash-low"},
    "easy": {"gemini-3.8-flash-medium"},
    "medium": {"gemini-4-hydrogen-medium"},
    "medium-hard": {"gemini-4-argon-high", "gemini-4-hydrogen-high"},
    "complex": {"gemini-4-neon-high"},
    "holy-fuck": {"gemini-4-neon-high"},
}
NON_AGY_BANDS = ("medium", "medium-hard", "complex", "holy-fuck")


@pytest.mark.parametrize("band", BANDS)
def test_agy_automatic_work_pool_matches_approved_matrix(band):
    registry = load_registry(SOURCE_REGISTRY)
    agy_models = set(registry["agents"]["agy"]["model"]["known_models"])
    pool = set(routing.automatic_policy_model_ids(registry, band)) & agy_models
    assert pool == AGY_WORK_MATRIX[band]


@pytest.mark.parametrize("role", ["impl", "modify", "review", "artifact-recovery", "merge-recovery"])
@pytest.mark.parametrize("band", BANDS)
def test_forced_agy_work_uses_only_designated_models_at_effective_band(band, role):
    selection = select_route(
        RouteRequest(role=role, complexity_level=band, forced_agent="agy"),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    effective = routing.routing_level(role, band)
    assert selection["routing_level"] == effective
    assert selection["model"] in AGY_WORK_MATRIX[effective]
    assert {c["model"] for c in selection["evaluated_candidates"]} == AGY_WORK_MATRIX[effective]


@pytest.mark.parametrize("band", NON_AGY_BANDS)
@pytest.mark.parametrize("role", ["impl", "review"])
def test_agy_is_never_automatic_above_easy_while_other_agents_are_present(band, role):
    selection = select_route(
        RouteRequest(role=role, complexity_level=band), load_registry(SOURCE_REGISTRY), {},
    ).selection
    assert selection["agent"] != "agy"


@pytest.mark.parametrize("band", NON_AGY_BANDS)
def test_agy_fallback_takes_designated_model_when_it_is_the_only_agent(band):
    selection = select_route(
        RouteRequest(role="impl", complexity_level=band, detected_agents=frozenset({"agy"})),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    assert selection["agent"] == "agy"
    assert selection["model"] in AGY_WORK_MATRIX[band]


def test_unavailable_designated_agy_model_falls_back_to_sibling_at_band():
    selection = select_route(
        RouteRequest(
            role="impl",
            complexity_level="medium-hard",
            forced_agent="agy",
            availability={"models": {"gemini-4-argon-high"}},
        ),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    assert selection["model"] == "gemini-4-hydrogen-high"


def test_routing_availability_excludes_model_pair_without_disabling_agy(tmp_path):
    path = tmp_path / "availability.json"
    path.write_text(json.dumps({"unavailable": [{"agent": "agy", "model": "gemini-4-argon-high"}]}))
    availability = routing.routing_availability(load_registry(SOURCE_REGISTRY), "", str(path))
    selection = select_route(
        RouteRequest(role="impl", complexity_level="medium-hard", forced_agent="agy", availability=availability),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection

    assert selection["model"] == "gemini-4-hydrogen-high"
    assert availability["agents"] == {"github-copilot"}
    assert availability["pairs"] == {("agy", "gemini-4-argon-high")}


def test_routing_availability_excludes_unavailable_agy_harness(tmp_path):
    path = tmp_path / "availability.json"
    path.write_text(json.dumps({"deny_agents": ["agy"]}))
    availability = routing.routing_availability(load_registry(SOURCE_REGISTRY), "", str(path))
    selection = select_route(
        RouteRequest(role="impl", complexity_level="medium-hard", availability=availability),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection

    assert selection["agent"] != "agy"
    with pytest.raises(SystemExit) as exc:
        select_route(
            RouteRequest(role="impl", complexity_level="medium-hard", forced_agent="agy", availability=availability),
            load_registry(SOURCE_REGISTRY),
            {},
        )
    assert exc.value.code == 2


@pytest.mark.parametrize("band", BANDS)
def test_planning_never_routes_agy_forced_or_automatic(band):
    registry = load_registry(SOURCE_REGISTRY)
    with pytest.raises(SystemExit) as exc:
        select_route(RouteRequest(role="plan", complexity_level=band, forced_agent="agy"), registry, {})
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        select_route(RouteRequest(role="plan", complexity_level=band, detected_agents=frozenset({"agy"})), registry, {})
    assert exc.value.code == 2


@pytest.mark.parametrize("model", ["gemini-4-hydrogen-medium", "gemini-4-argon-high", "gemini-4-hydrogen-high", "gemini-4-neon-high"])
def test_planning_rejects_forced_agy_models_from_new_matrix(model):
    with pytest.raises(SystemExit) as exc:
        select_route(RouteRequest(role="plan", complexity_level="medium-hard", forced_model=model),
                     load_registry(SOURCE_REGISTRY), {})
    assert exc.value.code == 2


@pytest.mark.parametrize("band", BANDS)
def test_normal_planning_selection_stays_non_agy_at_every_band(band):
    selection = select_route(
        RouteRequest(role="plan", complexity_level=band), load_registry(SOURCE_REGISTRY), {},
    ).selection
    assert selection["agent"] in {"codex", "claude"}
    assert all(candidate["agent"] != "agy" for candidate in selection["evaluated_candidates"])


def test_explicit_valid_band_override_still_routes_non_agy_planning():
    selection = select_route(
        RouteRequest(role="plan", complexity_level="holy-fuck", forced_agent="claude", forced_model="claude-sonnet-5-5"),
        load_registry(SOURCE_REGISTRY), {},
    ).selection
    assert (selection["agent"], selection["model"], selection["routing_level"]) == (
        "claude", "claude-sonnet-5-5", "holy-fuck",
    )


def test_agy_sticky_reviewer_and_usage_attribution_survive_escalated_band():
    registry = load_registry(SOURCE_REGISTRY)
    ledger = routing.normalize_ledger({})
    first = select_route(
        RouteRequest(role="review", complexity_level="medium-hard", detected_agents=frozenset({"agy"})),
        registry, ledger,
    ).selection
    assert (first["routing_level"], first["model"]) == ("complex", "gemini-4-neon-high")
    routing.append_decision(ledger, first)
    assert ledger["totals"]["agents"]["agy"] == 1
    assert ledger["totals"]["roles"]["review"]["agents"]["agy"] == 1
    sticky = select_route(
        RouteRequest(role="review", complexity_level="medium-hard", prefer_model="gemini-4-neon-high"),
        registry, ledger,
    ).selection
    assert (sticky["agent"], sticky["model"]) == ("agy", "gemini-4-neon-high")
    assert sticky["selection_reason"] == "sticky-reviewer-preference"


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


AGY_EXPLICIT_MODELS = (
    *(f"gemini-4-argon-{effort}" for effort in ("low", "medium", "high")),
    *(f"gemini-4-{family}-{effort}" for family in ("neon", "hydrogen") for effort in ("low", "medium", "high", "xhigh")),
    *(f"gemini-4-carbon-{effort}" for effort in ("low", "medium", "high")),
    *(f"gemini-3.8-flash-{effort}" for effort in ("low", "medium", "high")),
    *(f"claude-opus-5-5-{effort}" for effort in ("low", "medium", "high")),
    *(f"claude-sonnet-5-5-{effort}" for effort in ("low", "medium", "high")),
    "gpt-oss-120b-medium",
)


@pytest.mark.parametrize("model", AGY_EXPLICIT_MODELS)
def test_every_agy_model_can_be_explicitly_routed_at_an_ineligible_band(model):
    selection = select_route(
        RouteRequest(
            role="impl", complexity_level="medium", forced_agent="agy", forced_model=model,
        ),
        load_registry(SOURCE_REGISTRY),
        {},
    ).selection
    assert (selection["agent"], selection["model"]) == ("agy", model)


@pytest.mark.parametrize("model", [
    "gemini-3.1-pro-high", "gemini-3.1-pro-low", "gemini-3.5-flash-high",
    "gemini-3.5-flash-medium", "gpt-0ss-120b-medium",
])
def test_retired_agy_models_fail_instead_of_aliasing(model):
    with pytest.raises(SystemExit) as exc:
        select_route(
            RouteRequest(role="impl", complexity_level="medium", forced_agent="agy", forced_model=model),
            load_registry(SOURCE_REGISTRY),
            {},
        )
    assert exc.value.code == 2


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
    assert counts == {"codex":78,"claude":42}  # Captured legacy policy; medium AGY (0%) is fallback-only.
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
    ("holy-fuck", "claude-fable-5-2", "high"),
])
def test_claude_routes_use_current_model_families(band, model, effort):
    decision = select_route(RouteRequest(role="impl", complexity_level=band, forced_agent="claude"), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model, decision.effort) == ("claude", model, effort)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5"])
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
    ("medium-hard", "gpt-6.2-sol", "high"),
    ("complex", "gpt-6.2-sol", "high"),
    ("holy-fuck", "gpt-6.2-astra", "high"),
])
def test_codex_routes_use_requested_model_families(band, model, effort):
    decision = select_route(RouteRequest(role="impl", complexity_level=band, forced_agent="codex"), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model, decision.effort) == ("codex", model, effort)


@pytest.mark.parametrize("band, model, effort", [
    ("quite-easy", "gpt-6-luna", "high"), ("easy", "gpt-6-luna", "high"), ("medium", "gpt-6.1-terra", "high"),
    ("medium-hard", "gpt-6.2-sol", "high"), ("complex", "gpt-6.2-sol", "high"), ("holy-fuck", "gpt-6.2-sol", "high"),
])
def test_codex_complexity_scoring_has_a_supported_model_at_every_band(band, model, effort):
    decision = select_route(RouteRequest(role="complexity", complexity_level=band, forced_agent="codex"), load_registry(SOURCE_REGISTRY), {})
    assert (decision.agent, decision.model, decision.effort) == ("codex", model, effort)


@pytest.mark.parametrize("model", ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6.1-terra", "gpt-6-luna"])
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


@pytest.mark.parametrize("agent, model", [("claude", "claude-sonnet-5-5"), ("codex", "gpt-6.2-sol")])
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


def _pinned_run(tmp_path, python=None):
    run_dir = tmp_path / "project" / ".run-with-it"
    run_dir.mkdir(parents=True)
    (run_dir / "engine.json").write_text(json.dumps({
        "schema_version": 1, "engine": "python", "engine_version": 1, "python": python or sys.executable,
    }))
    return run_dir / "usage-ledger.json"


def _stale_registry(tmp_path):
    stale = tmp_path / "stale"
    stale.mkdir()
    source = Path(str(resource_path("agent-registry.json")))
    (stale / "agent-definitions.json").write_text(source.with_name("agent-definitions.json").read_text())
    path = stale / "agent-registry.json"
    path.write_text(source.read_text().replace("claude-fable-5-2", "claude-fable-5-1"))
    return path


def _route_args(ledger, *extra):
    return [*extra, "--ledger-file", str(ledger), "--role", "review", "--complexity-level", "complex", "--record"]


def test_pinned_run_rejects_registry_from_another_install(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_REGISTRY_FILE", raising=False)
    ledger = _pinned_run(tmp_path)
    with pytest.raises(SystemExit) as exc:
        routing.main(_route_args(ledger, "--registry-file", str(_stale_registry(tmp_path))))
    assert exc.value.code == 2
    assert "does not match" in capsys.readouterr().err
    assert not ledger.exists()


def test_pinned_run_defaults_to_runtime_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_REGISTRY_FILE", raising=False)
    ledger = _pinned_run(tmp_path)
    assert routing.main(_route_args(ledger)) == 0
    candidates = json.loads(capsys.readouterr().out)["evaluated_candidates"]
    assert "claude-fable-5-1" not in {candidate["model"] for candidate in candidates}


def test_pinned_run_accepts_copy_of_runtime_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_REGISTRY_FILE", raising=False)
    ledger = _pinned_run(tmp_path)
    assert routing.main(_route_args(ledger, "--registry-file", str(resource_path("agent-registry.json")))) == 0


def test_pinned_run_honors_explicit_registry_override(tmp_path, monkeypatch, capsys):
    override = _stale_registry(tmp_path)
    monkeypatch.setenv("AGENT_REGISTRY_FILE", str(override))
    ledger = _pinned_run(tmp_path)
    assert routing.main(_route_args(ledger)) == 0
    candidates = json.loads(capsys.readouterr().out)["evaluated_candidates"]
    assert "claude-fable-5-2" not in {candidate["model"] for candidate in candidates}
    assert routing.main(_route_args(ledger, "--registry-file", str(override))) == 0


def test_pinned_run_refuses_a_different_interpreter(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_REGISTRY_FILE", raising=False)
    ledger = _pinned_run(tmp_path, python=str(tmp_path / "other-python"))
    with pytest.raises(SystemExit):
        routing.main(_route_args(ledger))
    assert "pinned to" in capsys.readouterr().err
    assert not ledger.exists()
