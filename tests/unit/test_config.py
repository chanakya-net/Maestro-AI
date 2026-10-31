from ai_skills.config import RuntimeConfig


def test_cli_precedes_environment_and_defaults():
    config = RuntimeConfig.from_sources(
        {"forced_agent": "cli", "parallel_jobs": 2},
        {"FORCED_AGENT": "environment", "FORCED_MODEL": "model", "PARALLEL_JOBS": "8"},
    )
    assert config.forced_agent == "cli"
    assert config.forced_model == "model"
    assert config.parallel_jobs == 2
    assert config.sub_coord_agent == "codex"
    assert config.sub_coord_model == "gpt-6.1-sol"


import json
import pytest


@pytest.mark.parametrize("cli,env", [({"parallel_jobs": 0}, {}), ({}, {"PARALLEL_JOBS": "nonsense"}), ({"poll_interval_seconds": -1}, {}), ({"poll_interval_seconds": float("nan")}, {}), ({"parallel_jobs": True}, {})])
def test_invalid_runtime_values_rejected(cli, env):
    with pytest.raises(ValueError):
        RuntimeConfig.from_sources(cli, env)


def test_deprecated_worker_aliases_are_separate_from_sub_coordinator():
    with pytest.warns(DeprecationWarning):
        config = RuntimeConfig.from_sources({}, {"AGENT": "legacy-worker", "MODEL": "legacy-model", "SUB_COORD_AGENT": "claude", "SUB_COORD_MODEL": "sub-model"})
    assert (config.forced_agent, config.forced_model) == ("legacy-worker", "legacy-model")
    assert (config.sub_coord_agent, config.sub_coord_model) == ("claude", "sub-model")
    sub_only = RuntimeConfig.from_sources({}, {"SUB_COORD_AGENT": "claude", "SUB_COORD_MODEL": "sub"})
    assert sub_only.forced_agent is None and sub_only.forced_model is None


def test_explicit_worker_overrides_win_over_deprecated_aliases():
    config = RuntimeConfig.from_sources({"forced_agent": "cli"}, {"FORCED_MODEL": "forced", "AGENT": "old", "MODEL": "old"})
    assert (config.forced_agent, config.forced_model) == ("cli", "forced")


def test_foreground_and_detached_configs_roundtrip_without_credentials():
    config = RuntimeConfig.from_sources({"forced_agent": "codex", "github_token": "secret"}, {"OPENAI_API_KEY": "secret", "SUB_COORD_MODEL": "sub", "PARALLEL_JOBS": "7"})
    record = config.to_record()
    assert record["schema_version"] == 1
    assert "secret" not in json.dumps(record)
    assert RuntimeConfig.from_record(json.loads(json.dumps(record))) == config
    record["OPENAI_API_KEY"] = "secret"
    assert "secret" not in json.dumps(RuntimeConfig.from_record(record).to_record())
    with pytest.raises(ValueError):
        RuntimeConfig.from_record({"schema_version": 900})


def test_cli_deprecated_alias_still_precedes_environment():
    with pytest.warns(DeprecationWarning):
        config = RuntimeConfig.from_sources({"agent": "cli-alias"}, {"FORCED_AGENT": "env"})
    assert config.forced_agent == "cli-alias"
