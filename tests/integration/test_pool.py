import json
from pathlib import Path
import pytest
from ai_skills.pool import PoolConfig, select_ready

def test_select_ready_respects_dependencies_scopes_and_slots(tmp_path):
    context=tmp_path/'context.md';context.write_text('context')
    state={'execution_plan':{'topo_order':[1,2,3],'concurrency_policy':'strict'},'issue_registry':{
        '1':{'status':'pending','deps':[],'context_file':str(context),'parallel_safe':True,'ownership_scope':['src/a']},
        '2':{'status':'pending','deps':[],'context_file':str(context),'parallel_safe':True,'ownership_scope':['src/a/sub']},
        '3':{'status':'pending','deps':[1],'context_file':str(context),'parallel_safe':True,'ownership_scope':['src/c']}}}
    result=select_ready(state,limit=2)
    assert result.issues == ('1',)
    assert '2' in result.deferrals

from ai_skills.pool import PoolSupervisor
from pool_support import pool_fixture,FakePoolDriver,install_fake

@pytest.mark.parametrize('value',[0,-1,True,1.5])
def test_invalid_parallel_cap_rejected(tmp_path,value):
    with pytest.raises(ValueError):PoolConfig(state_file=tmp_path/'state',parallel_jobs=value)

@pytest.mark.parametrize('value',[0,-1,float('inf'),float('nan'),True])
def test_invalid_poll_interval_rejected(tmp_path,value):
    with pytest.raises(ValueError):PoolConfig(state_file=tmp_path/'state',poll_seconds=value)

def test_subcoord_selectors_do_not_inherit_worker_forced_values(tmp_path):
    config=PoolConfig.from_sources({'state_file':str(tmp_path/'state'),'agent':'coordinator','model':'coordinator-model'},
        {'FORCED_AGENT':'worker','FORCED_MODEL':'worker-model','SUB_COORD_AGENT':'env-coord','SUB_COORD_MODEL':'env-model'})
    assert config.sub_coord_agent=='coordinator' and config.sub_coord_model=='coordinator-model'
    assert config.environment['FORCED_AGENT']=='worker'

def test_all_nondefault_configuration_values_survive_detach_record(tmp_path):
    config=PoolConfig(state_file=tmp_path/'state',parallel_jobs=3,sub_coord_agent='fake',sub_coord_model='custom',effort='xhigh',
        poll_seconds=.4,timeout_seconds=42,hard_limit_seconds=43,quiet_seconds=7,stall_seconds=8,bootstrap_seconds=9,
        max_recovery_attempts=4,max_compaction_handoffs=8,max_worker_wait_seconds=99,worker_stale_seconds=77,
        permission_mode='safe',gui_mode='1',environment={'SECRET_TOKEN':'never persist','FORCED_MODEL':'worker'})
    record=config.to_record()
    assert 'SECRET_TOKEN' not in json.dumps(record)
    restored=PoolConfig.from_record(record)
    assert restored.to_record()==record
    assert restored.environment is None

def test_pool_cli_config_overrides_coordinator_aliases(tmp_path,monkeypatch):
    import ai_skills.pool as pool
    config=PoolConfig(state_file=tmp_path/'state',sub_coord_agent='old',sub_coord_model='old-model')
    path=tmp_path/'config.json';path.write_text(json.dumps(config.to_record()))
    seen=[];monkeypatch.setattr(pool,'run_pool',lambda value:seen.append(value) or pool.PoolResult('validated'))
    assert pool.main(['--config',str(path),'--agent','new','--model','new-model','--poll-seconds','.2'])==0
    assert seen[0].sub_coord_agent=='new' and seen[0].sub_coord_model=='new-model'
    assert seen[0].poll_seconds==.2

@pytest.mark.parametrize('payload',[{'schema_version':True},{'schema_version':2}])
def test_pool_record_schema_is_strict(tmp_path,payload):
    with pytest.raises(ValueError):PoolConfig.from_record({**PoolConfig(state_file=tmp_path/'state').to_record(),**payload})

def test_worker_selector_overrides_are_explicit_serialized_configuration(tmp_path):
    config=PoolConfig.from_sources({'state_file':str(tmp_path/'state')},{'FORCED_AGENT':'worker','FORCED_MODEL':'worker-model','SUB_COORD_AGENT':'coordinator','SUB_COORD_MODEL':'coordinator-model'})
    record=config.to_record()
    assert record['forced_agent']=='worker' and record['forced_model']=='worker-model'
    restored=PoolConfig.from_record(record)
    assert restored.forced_agent=='worker' and restored.sub_coord_agent=='coordinator'

def test_environment_zero_does_not_enable_detach(tmp_path):
    config=PoolConfig.from_sources({'state_file':str(tmp_path/'state')},{'RUN_WITH_IT_DETACH':'0'})
    assert config.detach is False
