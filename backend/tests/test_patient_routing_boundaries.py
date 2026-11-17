import json

import pytest

from backend import config, llm_client, semantic_understanding as su, smart_doctor as sd
from backend.patient_router import decide


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    def install(task, *, entities=None, fields=None, scope='public', operations=None):
        payload = {'intent': 'lab', 'task': task, 'scope': scope,
                   'entities': entities or [], 'fields': fields or [], 'confidence': .95}
        if operations is not None:
            payload['operations'] = operations
        monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps(payload))
    return install


@pytest.mark.parametrize('question,route', [
    ('血糖正常吗', 'clarify'), ('我上次血糖是多少', 'personal_record'),
    ('心内科有哪些医生', 'general_query'), ('糖尿病患者有多少人', 'clarify'),
    ('本院糖尿病患者有多少人', 'general_query'),
    ('全国糖尿病患病率是多少', 'knowledge'),
    ('我想了解肺癌是什么，再查一下我上次的血糖', 'clarify'),
])
def test_unambiguous_scopes_and_ambiguous_requests_are_separated(model, question, route):
    model('clarify')
    understood = su.analyze(question)
    result = decide(question, subject_id='P001', understanding=understood)
    assert result.route == route
    if route == 'clarify':
        assert su.routing_choices(understood)


def test_record_action_in_fields_is_normalized_not_rejected(model):
    entity = {'name': '血糖', 'kind': 'lab', 'mention': '血糖'}
    model('record_query', entities=[entity], fields=['record_query'], scope='self')
    understood = su.analyze('能帮我调出我之前的血糖记录吗')
    assert understood.accepted
    assert decide('能帮我调出我之前的血糖记录吗', subject_id='P001', understanding=understood).route == 'personal_record'


def test_two_independent_operations_are_not_silently_reduced_to_one(model, monkeypatch):
    model('overview', entities=[{'name': '肺癌', 'kind': 'disease', 'mention': '肺癌'},
                               {'name': '血糖', 'kind': 'lab', 'mention': '血糖'}],
          fields=['overview', 'record_query'])
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('复合需求尚未选择，不得查库'))
    question = '肺癌是什么，也帮我调出我最近的血糖记录'
    understood = su.analyze(question)
    assert understood.task == 'clarify'
    reply = sd.ask(question, 'P001', understanding=understood)
    assert reply['data'] is None
    choices = su.routing_choices(understood)
    assert len(choices) == 2
    assert any('肺癌' in choice['question'] for choice in choices)
    assert any('血糖' in choice['question'] for choice in choices)


def test_clear_self_care_and_result_interpretation_are_not_split(model):
    from backend import dataset_kb
    question = '我的血糖高应该怎么控制'
    model('management', entities=[{'name': '血糖', 'kind': 'lab', 'mention': '血糖'}], fields=['treatments'])
    result = su.analyze(question)
    assert decide(question, subject_id='P001', understanding=result).route == 'knowledge'
    assert not su.routing_choices(result)


def test_knowledge_reference_does_not_need_rag_generation(model):
    model('reference', entities=[{'name': '血糖', 'kind': 'lab', 'mention': '血糖'}], fields=['reference'])
    result = su.analyze('血糖正常范围是多少')
    assert su.plan_for(result).strategy == 'structured_lookup'


def test_choices_never_grant_another_patient_access(model, monkeypatch):
    model('record_query', entities=[{'name': '血糖', 'kind': 'lab', 'mention': '血糖'}], scope='self')
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('他人记录不能变成本人查询'))
    result = su.analyze('帮我查我妈妈的血糖结果')
    assert not result.accepted
    assert not su.routing_choices(result)


def test_same_personal_record_followup_keeps_current_function(model):
    context = {'intent': 'lab', 'entity': '血糖', 'personal': True}
    result = su.analyze('血糖正常吗', context)
    assert decide('血糖正常吗', context, subject_id='P001', understanding=result).route == 'personal_record'
    assert not su.routing_choices(result)


def test_function_confirmation_is_not_missing_medical_evidence(model, monkeypatch):
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('确认功能前不得查询记录'))
    result = sd.ask('血糖正常吗', 'P001')
    assert result['interpretation']['source'] == '功能确认'
    assert result['evidence']['status'] == 'not_applicable'
    assert result['advice'] is None


def test_observed_nested_operation_metadata_is_safely_normalized(model):
    model('reference', entities=[{'name': '血糖', 'kind': 'lab', 'mention': '血糖'}], fields=['reference'],
          operations=[{'type': 'knowledge', 'fields': ['reference']}])
    result = su.analyze('血糖正常范围是多少')
    assert result.accepted
    assert decide('血糖正常范围是多少', subject_id='P001', understanding=result).route == 'knowledge'


def test_nested_operation_cannot_smuggle_sql_or_identity(model):
    model('reference', entities=[{'name': '血糖', 'kind': 'lab', 'mention': '血糖'}], fields=['reference'],
          operations=[{'type': 'knowledge', 'sql': 'SELECT * FROM patients'}])
    assert not su.analyze('血糖正常范围是多少').accepted


def test_people_count_followup_reuses_topic_but_still_confirms_scope(model):
    context = {'intent': 'disease', 'entity': '肺癌', 'personal': False}
    result = su.analyze('这个病有多少人', context)
    assert decide('这个病有多少人', context, subject_id='P001', understanding=result).route == 'clarify'
    assert result.entities[0].name == '肺癌'
