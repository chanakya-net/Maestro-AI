"""The shared fake executable supplies portable, provider-free test behavior."""
import json
import os
import subprocess
import sys


def test_fake_records_unicode_arguments_cwd_and_selected_env(fake_agent_path, tmp_path):
    record = tmp_path / "record.json"
    result = subprocess.run([sys.executable, str(fake_agent_path), "--mode", "record",
                             "--record", str(record), "hello space", "雪"], cwd=tmp_path,
                            env={**os.environ, "AI_SKILLS_TEST_VALUE": "unicode 雪"},
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(record.read_text(encoding="utf-8"))
    assert payload["argv"][-2:] == ["hello space", "雪"]
    assert payload["cwd"] == str(tmp_path)
    assert payload["environment"] == {"AI_SKILLS_TEST_VALUE": "unicode 雪"}


def test_fake_structured_failure_and_artifact(fake_agent_path, tmp_path):
    failure = subprocess.run([sys.executable, str(fake_agent_path), "--mode", "failure", "--exit-code", "7"],
                             capture_output=True, text=True, timeout=10)
    assert failure.returncode == 7
    assert json.loads(failure.stderr)["status"] == "failed"
    artifact = tmp_path / "artifact.json"
    success = subprocess.run([sys.executable, str(fake_agent_path), "--mode", "artifact", "--artifact", str(artifact)],
                             capture_output=True, text=True, timeout=10)
    assert success.returncode == 0
    assert json.loads(artifact.read_text())["status"] == "success"


def test_fake_partial_quiet_and_descendant_modes(fake_agent_path):
    for mode in ("partial", "quiet", "descendant", "ignore-termination"):
        result = subprocess.run([sys.executable, str(fake_agent_path), "--mode", mode, "--seconds", ".05"],
                                capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        if mode == "partial":
            assert result.stdout == b"partial output"
        elif mode == "descendant":
            payload = json.loads(result.stdout)
            assert payload["pid"] > 0
            assert payload["child_pid"] > 0
            assert payload["pid"] != payload["child_pid"]
        else:
            assert result.stdout == b""
