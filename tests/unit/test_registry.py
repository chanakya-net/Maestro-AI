"""Registry edits must reach both model selection and CLI invocation."""
import json

import pytest

from ai_skills import routing
from ai_skills.runner import AgentRunConfig, build_invocation, load_registry


@pytest.fixture
def registry_file(tmp_path):
    definitions = {
        "schema_version": 1,
        "defaults": {"model": {"flag_template": "--model {{model}}"}},
        "aliases": {"codex-cli": "codex"},
        "agents": {"codex": {
            "invocation": {"command": "codex", "args_template": ["{{model_flag}}", "{{model_settings}}", "-"], "prompt_transport": "stdin"},
            "permission_modes": {"default": ""},
        }},
    }
    (tmp_path / "agent-definitions.json").write_text(json.dumps(definitions))
    registry = {
        "schema_version": 2,
        "agent_definitions": "agent-definitions.json",
        "model_catalog": {
            "example": {"agents": ["codex"], "provider": "openai", "complexity_weight": 5,
                        "work_bands": ["medium"], "effort_by_band": {"medium": "xhigh"}},
            "example-alias": {"alias_of": "example", "agents": []},
        },
        "model_routing": {
            "score_to_weight": [{"label": "medium", "score_min": 18, "score_max": 22, "weight_min": 4, "weight_max": 6}],
            "usage_distribution": {
                "default_target_percent": {"codex": 100},
                "non_complexity_band_target_percent": {"default": {"codex": 100}},
            },
        },
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry))
    return path


def test_single_model_entry_controls_routing_and_runner(registry_file, tmp_path):
    registry = load_registry(registry_file)
    decision = routing.select_route(routing.RouteRequest(role="impl"), registry, {})
    assert (decision.agent, decision.model, decision.effort) == ("codex", "example", "xhigh")
    assert registry["agents"]["codex"]["model"]["known_models"] == ["example"]
    context = tmp_path / "context.md"
    prompt = tmp_path / "prompt.md"
    context.write_text("context")
    prompt.write_text("prompt")
    spec = build_invocation(AgentRunConfig(agent="codex-cli", model=decision.model, effort=decision.effort,
        repo_root=tmp_path, context_file=context, prompt_file=prompt, gui_mode="0", environment={}), registry)
    assert spec.argv == ("codex", "--model", "example", "-c", "model_reasoning_effort=xhigh", "-")


def test_model_alias_inherits_metadata_without_inheriting_agent_membership(registry_file):
    registry = load_registry(registry_file)
    assert registry["model_catalog"]["example-alias"]["provider"] == "openai"
    assert registry["model_catalog"]["example-alias"]["complexity_weight"] == 5
    assert "example-alias" not in registry["agents"]["codex"]["model"]["known_models"]


def test_legacy_custom_registry_still_loads(tmp_path):
    payload = {"schema_version": 1, "agents": {"custom": {"model": {"known_models": ["custom-model"]}}}}
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload))
    assert load_registry(path) == payload


def test_expanded_registry_can_be_saved_as_a_self_contained_override(registry_file, tmp_path):
    registry = load_registry(registry_file)
    directory = tmp_path / "standalone"
    directory.mkdir()
    override = directory / "registry.json"
    override.write_text(json.dumps(registry))
    assert load_registry(override) == registry


@pytest.mark.parametrize("damage, message", [
    ("unknown-agent", "unknown agent"),
    ("unknown-alias", "unknown model alias"),
    ("alias-cycle", "model alias cycle"),
    ("bad-band", "unknown routing band"),
])
def test_registry_errors_are_reported_before_routing(registry_file, damage, message):
    payload = json.loads(registry_file.read_text())
    model = payload["model_catalog"]["example"]
    if damage == "unknown-agent":
        model["agents"] = ["missing"]
    elif damage == "unknown-alias":
        model["alias_of"] = "missing"
    elif damage == "alias-cycle":
        model["alias_of"] = "example-alias"
    else:
        model["work_bands"] = ["typo"]
    registry_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=message):
        load_registry(registry_file)


def test_missing_cli_definitions_fail_instead_of_using_bundled_defaults(registry_file):
    registry_file.with_name("agent-definitions.json").unlink()
    with pytest.raises(FileNotFoundError):
        load_registry(registry_file)


def test_unsupported_strategy_does_not_silently_use_another_algorithm(registry_file):
    payload = json.loads(registry_file.read_text())
    payload["model_routing"]["selection_strategy"] = "random"
    registry_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unsupported selection_strategy"):
        load_registry(registry_file)


@pytest.mark.parametrize("field, value", [("agents", "codex"), ("work_bands", None), ("complexity_bands", "medium"), ("effort_by_band", [])])
def test_model_membership_and_effort_require_the_documented_types(registry_file, field, value):
    payload = json.loads(registry_file.read_text())
    payload["model_catalog"]["example"][field] = value
    registry_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=field):
        load_registry(registry_file)


@pytest.mark.parametrize("damage", ["agent-model", "routing", "usage", "alias", "effort"])
def test_malformed_nested_configuration_reports_a_validation_error(registry_file, damage):
    payload = json.loads(registry_file.read_text())
    if damage == "agent-model":
        definitions_path = registry_file.with_name("agent-definitions.json")
        definitions = json.loads(definitions_path.read_text())
        definitions["agents"]["codex"]["model"] = None
        definitions_path.write_text(json.dumps(definitions))
    elif damage == "routing":
        payload["model_routing"] = []
    elif damage == "usage":
        payload["model_routing"]["usage_distribution"] = []
    elif damage == "alias":
        payload["model_catalog"]["example-alias"]["alias_of"] = ["example"]
    else:
        payload["model_catalog"]["example"]["effort_by_band"]["medium"] = ["xhigh"]
    registry_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_registry(registry_file)
