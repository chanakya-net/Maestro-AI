import pytest


def binding(tmp_path):
    return dict(run_id='run-7',attempt_id='attempt-1',issue='7',role='impl',
                state_file=tmp_path/'state.json',expected_artifacts={'result':str(tmp_path/'result.json')})


def test_pending_outcome_is_bound_without_secrets(tmp_path):
    from ai_skills.outcomes import pending_outcome, outcome_reason, outcome_path
    record=pending_outcome(**binding(tmp_path),started_at=123.0)
    assert record == {'schema_version':1,'run_id':'run-7','attempt_id':'attempt-1','issue':'7','role':'impl',
        'state_file':str(tmp_path/'state.json'),'expected_artifacts':{'result':str(tmp_path/'result.json')},
        'status':'pending','started_at':123.0,'finished_at':None,'provider_return_code':None,
        'worker_return_code':None,'reason':'','failure_class':''}
    assert outcome_reason(record,**binding(tmp_path)) == ''
    assert outcome_path(tmp_path/'state.json') == tmp_path/'state.json.outcome.json'


@pytest.mark.parametrize('patch', [
    {'schema_version':2},{'schema_version':True},{'run_id':'stale'},{'attempt_id':'stale'},
    {'issue':'8'},{'role':'modify'},{'state_file':'relative'}, {'expected_artifacts':{}},
    {'status':'unknown'},{'started_at':float('nan')},{'finished_at':123}, {'worker_return_code':0},
    {'provider_return_code':0},{'reason':[]},{'failure_class':False},
])
def test_outcome_rejects_malformed_or_stale_bindings(tmp_path,patch):
    from ai_skills.outcomes import pending_outcome, outcome_reason
    record=pending_outcome(**binding(tmp_path),started_at=123.0)
    assert outcome_reason(dict(record,**patch),**binding(tmp_path))


@pytest.mark.parametrize('key',['finished_at','provider_return_code','worker_return_code'])
def test_pending_outcome_requires_nullable_fields(tmp_path,key):
    from ai_skills.outcomes import pending_outcome, outcome_reason
    record=pending_outcome(**binding(tmp_path)); del record[key]
    assert outcome_reason(record,**binding(tmp_path))


@pytest.mark.parametrize('status,code',[('success',0),('failed',1),('blocked',1),('interrupted',130)])
def test_outcome_validates_terminal_status_and_nonzero_failure(tmp_path,status,code):
    from ai_skills.outcomes import pending_outcome, outcome_reason
    record=pending_outcome(**binding(tmp_path),started_at=123.0)
    record.update(status=status,finished_at=124.,provider_return_code=0,worker_return_code=code,
                  reason='' if status=='success' else 'reported-failure',failure_class='capability')
    assert outcome_reason(record,**binding(tmp_path)) == ''
    if status!='success':
        record['worker_return_code']=0
        assert outcome_reason(record,**binding(tmp_path))
