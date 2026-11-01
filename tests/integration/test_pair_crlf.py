"""Frozen prompt hashes are byte-accurate across Git checkout line endings."""
from ai_skills.resources import resource_path
from test_pair import pair_fixture, invoke


def test_crlf_source_prompt_remains_valid_after_freezing(pair_fixture, monkeypatch, capsys):
    start, root, _ = pair_fixture
    prompt = root / 'crlf-prompt.md'
    original = resource_path('pair-colleague-agent-prompt.md').read_text(encoding='utf-8').replace('\r\n', '\n')
    payload = original.replace('\n', '\r\n').encode('utf-8')
    prompt.write_bytes(payload)
    monkeypatch.setenv('PAIR_COLLEAGUE_PROMPT_FILE', str(prompt))
    run = start()
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert (run / 'agent-prompt-template.md').read_bytes() == payload
