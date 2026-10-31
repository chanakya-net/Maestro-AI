"""Active instructions target the single installed native runtime on every OS."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
ACTIVE=[*sorted((ROOT/'assets').glob('*.md')),ROOT/'skills/run-with-it/SKILL.md',ROOT/'skills/pair-colleague/SKILL.md']


def test_active_instructions_have_no_legacy_runtime_entrypoints():
    bad=[]
    pattern=re.compile(r'(?:run-agent|worker-watch|pair-colleague|run-with-it-[\w-]+)\.(?:sh|ps1|py)\b|install\.(?:sh|ps1)\b')
    for path in ACTIVE:
        for number,line in enumerate(path.read_text().splitlines(),1):
            if pattern.search(line):bad.append(f'{path.relative_to(ROOT)}:{number}: {line}')
    assert bad==[],'\n'.join(bad)


def test_os_specific_orchestration_recipes_are_removed():
    for path in ACTIVE:
        text=path.read_text()
        assert not re.search(r'```(?:powershell|bash|sh)\b',text),str(path)
        assert not re.search(r'(?:ConvertFrom-Json|ConvertTo-Json|Start-Process|ForEach-Object|set -euo|\bnohup\b|\bsetsid\b)',text),str(path)


def test_critical_authorities_and_role_schema_contracts_remain():
    coordinator=(ROOT/'assets/coordinator-rules.md').read_text().lower()
    sub=(ROOT/'assets/sub-coordinator-prompt.md').read_text().lower()
    main=(ROOT/'assets/main-orchestrator-rules.md').read_text().lower()
    skill=(ROOT/'skills/run-with-it/SKILL.md').read_text().lower()
    for text in (coordinator,sub,main,skill):
        assert 'sub_coord_agent' in text and 'forced_agent' in text
        assert 'artifact' in text and 'verification' in text
    for name in ('complexity-prompt.md','plan-prompt.md','prompt.md','review-prompt.md','modifier-prompt.md'):
        text=(ROOT/'assets'/name).read_text()
        assert '```json' in text,name
    assert 'cumulative' in sub and 'sticky' in sub
    assert 'infrastructure' in sub and 'capability' in sub
    assert 'compaction' in sub and 'authorit' in sub
    assert 'check-in' in sub


def test_native_cli_examples_are_documented():
    text='\n'.join(path.read_text() for path in ACTIVE)
    for command in ('agent run','worker dispatch','worker inspect','run pool','run watch','run stop','state','route','artifact','github','report','pair','git prepare-issue','git merge-issue'):
        assert 'ai-skills '+command in text,command


def test_all_documented_json_objects_parse_and_authority_twins_match():
    import json
    authority=[]
    for path in ACTIVE:
        text=path.read_text()
        for match in re.finditer(r'```json\n(.*?)\n```',text,re.S):
            assert isinstance(json.loads(match.group(1)),dict),str(path)
        match=re.search(r'<!-- NATIVE_AUTHORITY_START:.*?-->\n(.*?)<!-- NATIVE_AUTHORITY_END -->',text,re.S)
        if match:authority.append(match.group(1))
    assert len(authority)==4
    assert len(set(authority))==1


def test_documented_worker_json_validates_with_fake_provider(tmp_path,capsys):
    import json
    import os
    import sys
    from ai_skills.dispatch import WorkerConfig,main
    text=(ROOT/'assets/sub-coordinator-prompt.md').read_text()
    examples=[json.loads(match.group(1)) for match in re.finditer(r'```json\n(.*?)\n```',text,re.S)]
    worker=next(value for value in examples if 'context_file' in value and 'agent' in value)
    for key,value in tuple(worker.items()):
        if isinstance(value,str) and value.startswith('/absolute/'):
            worker[key]=str(tmp_path/key)
    worker['repo_root']=str(tmp_path)
    for key in ('context_file','prompt_file'):
        Path(worker[key]).write_text('fixture task',encoding='utf-8')
    registry=tmp_path/'registry.json'
    registry.write_text(json.dumps({'agents':{'codex':{'invocation':{'command':sys.executable,'args_template':['-c','raise AssertionError("validation must not launch")']},'permission_modes':{'default':''}}}}))
    worker['registry_file']=str(registry)
    config=tmp_path/'worker.json';config.write_text(json.dumps(worker))
    assert WorkerConfig.from_sources(worker,os.environ).agent_run.role=='impl'
    assert main(['dispatch','--config',str(config),'--validate-only'])==0
    assert not Path(worker['state_file']).exists()


def test_documented_pool_json_matches_native_configuration():
    import json
    from ai_skills.pool import PoolConfig
    text=(ROOT/'skills/run-with-it/SKILL.md').read_text()
    examples=[json.loads(match.group(1)) for match in re.finditer(r'```json\n(.*?)\n```',text,re.S)]
    sample=next(value for value in examples if 'sub_coord_agent' in value and 'state_file' in value)
    config=PoolConfig.from_record(sample)
    assert config.sub_coord_agent=='codex' and config.detach is True


def test_native_command_families_accept_help_without_launching():
    import subprocess
    import sys
    for command in [('agent','run'),('worker','dispatch'),('worker','inspect'),('worker','emit'),('run','pool'),('run','watch'),('run','stop'),
                    ('state',),('route',),('artifact',),('github',),('report',),('pair',),('git','prepare-issue'),('git','merge-issue')]:
        result=subprocess.run([sys.executable,'-m','ai_skills',*command,'--help'],cwd=ROOT,capture_output=True,text=True)
        assert result.returncode==0,(command,result.stderr)
