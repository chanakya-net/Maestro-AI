from ai_skills.routing import RouteRequest, select_route, routing_level

def test_role_band_bumps():
    assert routing_level("plan", "easy") == "medium-hard"
    assert routing_level("review", "complex") == "holy-fuck"

import copy
import json
from pathlib import Path
import pytest
from ai_skills.resources import resource_path
from ai_skills import routing
from ai_skills.registry import load_registry

GOLDEN = json.loads((Path(__file__).parents[1] / "fixtures" / "routing-golden.json").read_text())

@pytest.mark.parametrize("case", GOLDEN, ids=lambda case: str(case["request"]))
def test_golden_routing_policy(case):
    registry=load_registry()
    request=RouteRequest(**{key:frozenset(value) if isinstance(value,list) else value for key,value in case["request"].items()})
    if "exit_code" in case["expected"]:
        with pytest.raises(SystemExit) as exc: select_route(request,registry,{})
        assert exc.value.code == case["expected"]["exit_code"]
    else:
        selection=select_route(request,registry,{}).selection
        assert {key:selection[key] for key in case["expected"]} == case["expected"]

def test_distribution_and_sticky_reviewer():
    registry=load_registry()
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
    registry=load_registry()
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
    ("quite-easy", "claude-haiku-5-5", ""),
    ("easy", "claude-haiku-5-5", ""),
    ("medium", "claude-sonnet-5-5", "medium"),
    ("medium-hard", "claude-opus-5-5", "high"),
    ("complex", "claude-opus-5-5", "xhigh"),
    ("holy-fuck", "claude-fable-5-1", "max"),
])
def test_claude_routes_use_current_model_families(band, model, effort):
    decision = select_route(RouteRequest(role="impl", complexity_level=band, forced_agent="claude"), load_registry(), {})
    assert (decision.agent, decision.model, decision.effort) == ("claude", model, effort)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-5-5"])
def test_requested_claude_models_can_be_forced(model):
    decision = select_route(RouteRequest(role="impl", forced_agent="claude", forced_model=model), load_registry(), {})
    assert (decision.agent, decision.model) == ("claude", model)


@pytest.mark.parametrize("model", [
    "claude-haiku-4-5", "claude-haiku-4.5", "claude-sonnet-4-6", "claude-sonnet-4.6",
    "claude-sonnet-4-6[1m]", "claude-opus-4-6", "claude-opus-5", "claude-sonnet-5",
    "claude-opus-5-high", "claude-opus-5-xhigh", "claude-sonnet-4.6-thinking", "claude-opus-4.6-thinking",
])
def test_retired_claude_models_cannot_be_routed(model):
    with pytest.raises(SystemExit):
        select_route(RouteRequest(role="impl", forced_model=model), load_registry(), {})


@pytest.mark.parametrize("band, model, effort", [
    ("quite-easy", "gpt-6-luna", "high"),
    ("easy", "gpt-6-luna", "high"),
    ("medium", "gpt-6.1-terra", "high"),
    ("medium-hard", "gpt-6.1-sol", "high"),
    ("complex", "gpt-6.1-sol", "xhigh"),
    ("holy-fuck", "gpt-6-astra", "max"),
])
def test_codex_routes_use_requested_model_families(band, model, effort):
    decision = select_route(RouteRequest(role="impl", complexity_level=band, forced_agent="codex"), load_registry(), {})
    assert (decision.agent, decision.model, decision.effort) == ("codex", model, effort)


@pytest.mark.parametrize("band, model, effort", [
    ("quite-easy", "gpt-6-luna", "high"), ("easy", "gpt-6-luna", "high"), ("medium", "gpt-6.1-terra", "high"),
    ("medium-hard", "gpt-6.1-sol", "high"), ("complex", "gpt-6.1-sol", "xhigh"), ("holy-fuck", "gpt-6.1-sol", "xhigh"),
])
def test_codex_complexity_scoring_has_a_supported_model_at_every_band(band, model, effort):
    decision = select_route(RouteRequest(role="complexity", complexity_level=band, forced_agent="codex"), load_registry(), {})
    assert (decision.agent, decision.model, decision.effort) == ("codex", model, effort)


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-astra", "gpt-6.1-terra", "gpt-6-luna"])
def test_requested_codex_models_can_be_forced(model):
    decision = select_route(RouteRequest(role="impl", forced_agent="codex", forced_model=model), load_registry(), {})
    assert (decision.agent, decision.model) == ("codex", model)


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.4-mini", "gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.5"])
def test_retired_codex_models_cannot_be_routed_on_codex(model):
    with pytest.raises(SystemExit):
        select_route(RouteRequest(role="impl", forced_agent="codex", forced_model=model), load_registry(), {})
