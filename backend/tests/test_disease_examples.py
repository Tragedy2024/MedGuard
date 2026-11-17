import json

import pytest

from backend import config, llm_client, smart_doctor as sd
from backend import semantic_understanding as su
from backend.patient_router import decide


@pytest.fixture
def model(monkeypatch):
    calls = []
    def fake(*args, **kwargs):
        calls.append(1)
        return json.dumps({'intent': 'fallback', 'task': 'clarify', 'scope': 'unknown',
                           'entities': [], 'fields': [], 'confidence': 0.2})
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(llm_client, 'call_llm', fake)
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('疾病举例不读取患者记录'))
    monkeypatch.setattr(sd, '_retrieve_context', lambda *a, **k: pytest.fail('直接读取已有简介不需要初始化混合检索'))
    return calls


@pytest.mark.parametrize('question,count', [
    ('你能说明几种病', 12), ('请介绍几种常见疾病', 12), ('给我举几个疾病的例子', 12),
    ('介绍两种常见病', 2), ('请列举10种疾病', 10),
])
def test_general_disease_examples_have_sourced_short_introductions(model, question, count):
    understood = su.analyze(question)
    decision = decide(question, subject_id='P001', understanding=understood)
    reply = sd.ask(question, 'P001', understanding=understood)
    assert decision.route == 'knowledge'
    assert reply['retrieval_plan']['strategy'] == 'structured_lookup'
    assert len(reply['interpretation']['items']) == count
    assert all(i['ref_title'] and i['ref_excerpt'] and i['ref_source'] for i in reply['interpretation']['items'])
    assert reply['catalog']['returned_count'] == count
    assert '目录' in reply['interpretation']['title']
    assert reply['data'] is None and reply['audit_status'] == 'not_applicable'
    assert not model


def test_specific_classification_is_not_replaced_with_random_diseases(model):
    understood = su.analyze('糖尿病有几种')
    assert understood.task != 'disease_examples'


def test_symptoms_are_not_answered_as_a_disease_catalog(model):
    understood = su.analyze('我头痛恶心，可能得了哪种病')
    assert understood.task != 'disease_examples'


def test_model_can_recognize_other_wording_without_inventing_disease_names(model, monkeypatch):
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'disease', 'task': 'disease_examples', 'scope': 'public', 'entities': [],
        'fields': ['overview'], 'confidence': .96,
    }))
    reply = sd.ask('我想先认识一些疾病，简单聊聊可以吗', 'P001')
    assert reply['understanding']['status'] == 'model'
    assert len(reply['interpretation']['items']) == 12


def test_examples_work_without_model_configuration(monkeypatch):
    monkeypatch.setattr(config, 'LLM_API_KEY', '')
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', False)
    reply = sd.ask('你能说明几种病', 'P001')
    assert len(reply['interpretation']['items']) == 12


def test_examples_do_not_invent_a_single_followup_topic(model):
    reply = sd.ask('你能说明几种病', 'P001')
    assert reply['entity'] is None
    assert reply['understanding']['entities'] == []


def test_model_cannot_replace_personal_symptoms_with_random_examples(model, monkeypatch):
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'disease', 'task': 'disease_examples', 'scope': 'public', 'entities': [],
        'fields': ['overview'], 'confidence': .96,
    }))
    result = su.analyze('我不舒服，可能得了什么病')
    assert result.status == 'invalid'
