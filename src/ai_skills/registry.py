"""Expand the editable registry into the existing runner/router data contract.

CLI definitions change independently of model policy. Version 1 custom
registries remain self-contained; version 2 reads definitions beside itself.
"""
from copy import deepcopy
import json
from pathlib import Path

from ai_skills.resources import resource_path


BANDS = ("quite-easy", "easy", "medium", "medium-hard", "complex", "holy-fuck")


def _merge(defaults, overrides):
    result = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _object(source):
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("registry must contain a JSON object")
    return value


def load_registry(path=None):
    source = Path(path) if path else resource_path("agent-registry.json")
    registry = _object(source)
    version = registry.get("schema_version", 1)
    if version not in (1, 2):
        raise ValueError(f"unsupported registry schema_version: {version}")
    if version == 1:
        if not isinstance(registry.get("agents"), dict):
            raise ValueError("registry must contain an agents object")
        return registry

    filename = registry.get("agent_definitions")
    if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
        raise ValueError("agent_definitions must name a sibling JSON file")
    # Traversable resources need not be local Paths (for example, zip installs).
    definitions_source = Path(path).parent / filename if path else resource_path(filename)
    definitions = _object(definitions_source)
    if definitions.get("schema_version") != 1 or not isinstance(definitions.get("agents"), dict):
        raise ValueError("invalid agent definitions schema")
    defaults = definitions.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("agent defaults must be an object")
    agents = {}
    for agent_id, entry in definitions["agents"].items():
        if not isinstance(entry, dict):
            raise ValueError(f"agent definition must be an object: {agent_id}")
        agents[agent_id] = _merge(defaults, entry)
        if not isinstance(agents[agent_id].get("model", {}), dict):
            raise ValueError(f"agent model settings must be an object: {agent_id}")
        agents[agent_id].setdefault("model", {})["known_models"] = []

    catalog = registry.get("model_catalog")
    if not isinstance(catalog, dict):
        raise ValueError("registry must contain a model_catalog object")
    expanded = {}

    def expand(model_id, visiting=()):
        if model_id in visiting:
            raise ValueError(f"model alias cycle: {' -> '.join((*visiting, model_id))}")
        if model_id in expanded:
            return expanded[model_id]
        if model_id not in catalog:
            raise ValueError(f"unknown model alias: {model_id}")
        entry = catalog[model_id]
        if not isinstance(entry, dict):
            raise ValueError(f"model entry must be an object: {model_id}")
        parent = entry.get("alias_of")
        if parent is not None and not isinstance(parent, str):
            raise ValueError(f"alias_of must be a model ID string: {model_id}")
        base = deepcopy(expand(parent, (*visiting, model_id))) if parent else {}
        # These fields control eligibility rather than describe the model.
        # Aliases retain their exact CLI ID and declare their own membership.
        for key in ("agents", "work_bands", "required_bands"):
            base.pop(key, None)
        expanded[model_id] = _merge(base, {k: v for k, v in entry.items() if k != "alias_of"})
        return expanded[model_id]

    routing = deepcopy(registry.get("model_routing", {}))
    if not isinstance(routing, dict):
        raise ValueError("model_routing must be an object")
    if routing.get("selection_strategy", "usage-share-debt") != "usage-share-debt":
        raise ValueError("unsupported selection_strategy; the router uses usage-share-debt")
    policy = {band: {"models": [], **routing.get("non_complexity_band_policy", {}).get(band, {})} for band in BANDS}
    required = {band: [] for band in BANDS}
    efforts = {}
    models = {}
    for model_id in catalog:
        model = deepcopy(expand(model_id))
        for field in ("agents", "work_bands", "required_bands", "complexity_bands"):
            if field in model and (not isinstance(model[field], list) or any(not isinstance(value, str) for value in model[field])):
                raise ValueError(f"{field} must be a list of strings for model {model_id}")
        if "effort_by_band" in model and not isinstance(model["effort_by_band"], dict):
            raise ValueError(f"effort_by_band must be an object for model {model_id}")
        if any(not isinstance(value, str) for value in model.get("effort_by_band", {}).values()):
            raise ValueError(f"effort_by_band values must be strings for model {model_id}")
        for agent_id in model.pop("agents", []):
            if agent_id not in agents:
                raise ValueError(f"unknown agent {agent_id!r} for model {model_id}")
            agents[agent_id]["model"]["known_models"].append(model_id)
        for field in ("work_bands", "required_bands", "complexity_bands", "effort_by_band"):
            for band in model.get(field, []):
                if band not in BANDS:
                    raise ValueError(f"unknown routing band {band!r} for model {model_id}")
        for band in model.pop("work_bands", []):
            policy[band]["models"].append(model_id)
        for band in model.pop("required_bands", []):
            required[band].append(model_id)
        if "complexity_bands" in model:
            model["routing_bands"] = model.pop("complexity_bands")
        if "effort_by_band" in model:
            efforts[model_id] = model.pop("effort_by_band")
        models[model_id] = model
    routing["non_complexity_band_policy"] = policy
    routing["band_required_models"] = required
    routing["effort_by_model_and_band"] = efforts
    distribution = routing.get("usage_distribution", {})
    if not isinstance(distribution, dict):
        raise ValueError("usage_distribution must be an object")
    targets = distribution.get("non_complexity_band_target_percent", {})
    if "default" in targets:
        distribution["non_complexity_band_target_percent"] = {
            band: deepcopy(targets.get(band, targets["default"])) for band in BANDS
        }
    for role, targets in distribution.get("role_band_target_percent", {}).items():
        if "default" in targets:
            distribution["role_band_target_percent"][role] = {
                band: deepcopy(targets.get(band, targets["default"])) for band in BANDS
            }
    # The expanded value is self-contained and may itself be saved as an
    # override, so identify it using the existing version 1 runtime schema.
    return {"schema_version": 1, "aliases": deepcopy(definitions.get("aliases", {})),
            "agents": agents, "model_catalog": models, "model_routing": routing}
