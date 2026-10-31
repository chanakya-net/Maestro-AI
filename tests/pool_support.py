"""Deterministic pool fixtures; no provider executable or network connection."""
import json
from pathlib import Path
from ai_skills.pool import PoolConfig
from ai_skills.dispatch import WorkerResult
from ai_skills.storage import write_json_atomic

class FakePoolDriver:
    def __init__(self,outcomes=None,delays=None):
        self.outcomes=outcomes or {};self.delays=delays or {};self.started=[];self.workers={};self.maximum=0
    def dispatch(self,config):
        issue=str(config.agent_run.issue);sequence=self.outcomes.get(issue,['completed'])
        outcome=sequence[min(sum(str(old.agent_run.issue)==issue for old in self.started),len(sequence)-1)]
        self.started.append(config)
        self.workers[str(config.state_file)]={'config':config,'outcome':outcome,'ticks':self.delays.get(issue,0),'done':False}
        self.maximum=max(self.maximum,sum(not item['done'] for item in self.workers.values()))
        Path(config.state_file).parent.mkdir(parents=True,exist_ok=True)
        write_json_atomic(Path(str(config.state_file)+'.config.json'),config.to_record())
        write_json_atomic(config.state_file,{'state':'running'})
        return WorkerResult('running',0,Path(config.state_file))
    def adopt(self,path):
        item=self.workers[str(path)];config=item['config'];outcome=item['outcome']
        if item['ticks']:
            item['ticks']-=1
            return WorkerResult('running',0,Path(path))
        if not item['done']:
            item['done']=True
            if outcome in ('crash','compaction'):
                write_json_atomic(Path(config.agent_run.issue_dir)/'sub-state.json',{'phase':'impl','in_flight_agents':[], 'compaction_requested':outcome=='compaction'})
            else:
                write_json_atomic(Path(config.agent_run.result_file),{'outcome':outcome,'summary':'fixture '+outcome,'verification':{'passed':outcome=='completed'},'blocking_reasons':[]})
            write_json_atomic(Path(path),{'state':'failed' if outcome in ('crash','compaction') else 'completed','return_code':1 if outcome in ('crash','compaction') else 0})
        return WorkerResult('failed' if outcome in ('crash','compaction') else 'completed',1 if outcome in ('crash','compaction') else 0,Path(path))


def pool_fixture(tmp_path,issues,*,parallel=2,strict=False):
    registry={}
    for issue,info in issues.items():
        context=tmp_path/f'context-{issue}.md';context.write_text('fixture context')
        registry[str(issue)]={'status':'pending','deps':[],'context_file':str(context),**info}
    state=tmp_path/'.run-with-it'/'main-state.json';state.parent.mkdir(parents=True,exist_ok=True)
    state.write_text(json.dumps({'schema_version':4,'issue_registry':registry,'execution_plan':{'topo_order':list(issues),'parallel_jobs':parallel,'concurrency_policy':'strict' if strict else 'permissive'}}))
    return PoolConfig(state_file=state,parallel_jobs=parallel,poll_seconds=.001,heartbeat_seconds=.01,sub_coord_agent='fake',sub_coord_model='fixture')


def install_fake(monkeypatch,driver):
    import ai_skills.pool as pool
    monkeypatch.setattr(pool,'dispatch_worker',driver.dispatch)
    monkeypatch.setattr(pool,'adopt_worker',driver.adopt)
    monkeypatch.setattr(pool,'update_github',lambda request:None)


def real_pool_fixture(tmp_path,*,delay=.15,mode='completed'):
    import os,sys
    from dataclasses import replace
    config=pool_fixture(tmp_path,{1:{}},parallel=1)
    context=tmp_path/'context-1.md';context.write_text(f'OUTCOME={mode}\nDELAY={delay}\n')
    script=tmp_path/'provider.py'
    script.write_text('''import json,os,sys,time
from pathlib import Path
payload=sys.argv[1]
issue=os.environ['RUN_WITH_IT_ISSUE']
role=os.environ['RUN_WITH_IT_ROLE']
with Path('launches.log').open('a') as stream:
    stream.write(json.dumps({'issue':issue,'role':role,'forced_agent':os.environ.get('FORCED_AGENT'),'forced_model':os.environ.get('FORCED_MODEL')})+'\\n')
recovery='SUB_COORD_RECOVERY_MODE=1' in payload or role=='merge-recovery'
mode=next((line.split('=',1)[1] for line in payload.splitlines() if line.startswith('OUTCOME=')),'completed')
delay=float(next((line.split('=',1)[1] for line in payload.splitlines() if line.startswith('DELAY=')),'0'))
time.sleep(delay)
if mode in ('crash','compaction') and not recovery:
    path=Path(os.environ['RUN_WITH_IT_ISSUE_DIR'])/'sub-state.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'phase':'impl','in_flight_agents':[],'compaction_requested':mode=='compaction'}))
    raise SystemExit(1)
result=Path(os.environ['RUN_WITH_IT_RESULT_FILE']);result.parent.mkdir(parents=True,exist_ok=True)
result.write_text(json.dumps({'outcome':'completed' if recovery else mode,'summary':'real fake provider','verification':{'passed':True,'evidence':'fixture'}}))
print('STATUS|type=heartbeat|issue='+issue+'|role='+role+'|progress=done',flush=True)
''')
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({'agents':{'fake':{'invocation':{'command':sys.executable,'args_template':[str(script),'{{prompt}}']},'permission_modes':{'default':''},'model':{'default':'fixture','flag_template':''}}}}))
    prompt=tmp_path/'prompt.md';prompt.write_text('Write fixture report and exit.')
    return replace(config,registry_file=registry,prompt_file=prompt,merge_prompt_file=prompt,poll_seconds=.01,heartbeat_seconds=.05,bootstrap_seconds=5,hard_limit_seconds=5,environment=dict(os.environ, RUN_WITH_IT_GITHUB_UPDATES='0'))
