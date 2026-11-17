import json

import pytest

from backend import config, llm_client, semantic_understanding as su, smart_doctor as sd


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('症状咨询不得读本人记录'))
    def install(name, mention, *, task='triage', confidence=.72, intent='symptom', fields=None):
        payload = json.dumps({'intent': intent, 'task': task, 'scope': 'public',
            'entities': [{'name': name, 'kind': intent, 'mention': mention}],
            'fields': fields if fields is not None else ['triage'], 'confidence': confidence})
        monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: payload)
    return install


@pytest.mark.parametrize('question,name,mention,task,confidence,fields', [
    ('我头晕是啥病', '头晕', '头晕', 'triage', .72, ['triage']),
    ('我的头晕', '头晕', '头晕', 'clarify', .4, ['symptoms']),
    ('肚子疼怎么办', '腹痛', '肚子疼', 'triage', .65, ['triage']),
    ('我肚子不舒服', '腹部不适', '肚子不舒服', 'clarify', .4, []),
    ('我肚子疼该挂什么科', '腹痛', '肚子疼', 'department', .91, ['department']),
    ('头晕通常可以找哪个科看', '头晕', '头晕', 'department', .91, ['department']),
])
def test_recognized_symptom_receives_targeted_followup_not_parse_error(model, question, name, mention, task, confidence, fields):
    model(name, mention, task=task, confidence=confidence, fields=fields)
    reply = sd.ask(question, 'P001')
    assert reply['understanding']['status'] == 'model'
    assert reply['understanding']['task'] == 'triage'
    assert '未能可靠理解' not in reply['interpretation']['text']
    assert reply['interpretation']['items']
    assert all(i.get('ref_url', '').startswith('https://') for i in reply['interpretation']['items'])
    assert '多久' in reply['interpretation']['text'] or '多久' in reply['advice']['text']
    assert reply['data'] is None
    assert '相关疾病' not in reply['interpretation']['text']


def test_pneumonia_definition_uses_sourced_overview(model):
    model('肺炎', '肺炎', task='overview', confidence=.98, intent='disease', fields=['overview'])
    reply = sd.ask('肺炎是什么病', 'P001')
    assert reply['interpretation']['title'] == '肺炎是什么'
    assert '肺部' in reply['interpretation']['text']
    assert reply['retrieval_plan']['filters']['fields'] == ['overview']
    assert all(i.get('ref_url') for i in reply['interpretation']['items'])
    assert reply['evidence']['status'] == 'curated'


def test_low_confidence_cannot_authorize_record_access(model):
    model('血糖', '血糖', task='record_query', confidence=.4, intent='lab', fields=[])
    reply = sd.ask('查我的血糖结果', 'P001')
    assert reply['understanding']['status'] == 'invalid'
    assert reply['data'] is None


def test_symptom_followup_does_not_validate_inferred_disease(model):
    model('胃炎', '肚子不舒服', task='triage', confidence=.72, intent='disease')
    result = su.analyze('我肚子不舒服')
    assert not result.accepted


def test_other_symptoms_can_request_missing_information(model, monkeypatch):
    model('头痛', '头痛', task='clarify', confidence=.4, fields=[])
    monkeypatch.setattr(sd, '_retrieve_context', lambda *a, **k: pytest.fail('补问信息无需初始化检索器'))
    reply = sd.ask('我的头痛', 'P001')
    assert reply['understanding']['status'] == 'model'
    assert '头痛' in reply['interpretation']['text']
    assert '多久' in reply['interpretation']['text']


def test_symptom_reference_keeps_redflag_upgrade(model):
    model('腹痛', '肚子疼', confidence=.9)
    reply = sd.ask('肚子疼还呕血', 'P001')
    assert reply['advice']['urgent'] is True


def test_discomfort_remains_distinct_from_explicit_pain(model):
    model('腹部不适', '肚子不舒服', confidence=.72)
    reply = sd.ask('我肚子不舒服', 'P001')
    assert reply['understanding']['entities'] == ['腹部不适']
