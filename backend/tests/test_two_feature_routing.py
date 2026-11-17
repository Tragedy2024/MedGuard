import json

import pytest

from backend import config, llm_client, semantic_understanding as su
from backend.patient_router import decide
from tests.test_own_billing_and_result_routing import client  # noqa: F401 - shared database fixture


@pytest.mark.parametrize('question,feature,scope', [
    ('我想了解一下肺癌', 'smart_doctor', None),
    ('我看病花了多少钱', 'query_console', 'self'),
    ('我血糖正常吗', 'query_console', 'self'),
    ('心内科有哪些医生', 'query_console', 'hospital'),
    ('血糖正常吗', None, None),
])
def test_only_two_top_level_features(question, feature, scope, monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', False)
    result = su.analyze(question)
    decision = decide(question, subject_id='P001', understanding=result)
    assert decision.feature == feature
    assert decision.query_scope == scope


def test_confirmation_choices_use_two_features_with_scope_below(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', False)
    choices = su.routing_choices(su.analyze('血糖正常吗'))
    assert {c['label'] for c in choices} == {'智慧医生', '查询控制台'}
    assert {c['feature'] for c in choices} == {'smart_doctor', 'query_console'}
    assert next(c for c in choices if c['feature'] == 'query_console')['query_scope'] == 'self'


@pytest.mark.parametrize('question', ['我一共花的费用是多少2', '我一共花费了多少', '我花了多少钱'])
def test_correct_model_billing_intent_is_not_rejected_by_phrase_template(monkeypatch, question):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'billing', 'task': 'billing_query', 'scope': 'self', 'entities': [],
        'fields': [], 'confidence': .95, 'operations': ['personal_record'],
    }))
    result = su.analyze(question)
    assert result.accepted
    decision = decide(question, subject_id='P001', understanding=result)
    assert decision.feature == 'query_console' and decision.query_scope == 'self'


def test_two_queries_remain_scopes_of_query_console(monkeypatch):
    from backend.semantic_understanding import Understanding, Entity
    result = Understanding(status='model', attempted=True, intent='lab', task='clarify',
        entities=(Entity('血糖', 'lab'), Entity('肺癌', 'disease')),
        operations=('personal_record', 'general_query'))
    choices = su.routing_choices(result)
    assert {c['feature'] for c in choices} == {'query_console'}
    assert {c['query_scope'] for c in choices} == {'self', 'hospital'}


@pytest.mark.parametrize('question,feature,scope,interpretation', [
    ('肺癌是什么病', 'smart_doctor', None, None),
    ('我看病花了多少钱', 'query_console', 'self', None),
    ('我血糖正常吗', 'query_console', 'self', None),
    ('医生给我开的药怎么吃', 'query_console', 'self', None),
    ('心内科有哪些医生', 'query_console', 'hospital', None),
    ('血糖正常吗', None, None, None),
])
def test_api_returns_feature_scope_and_real_interpretation_chain(
        client, monkeypatch, question, feature, scope, interpretation):
    from tests.helpers import sign
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', False)
    monkeypatch.setattr(config, 'LLM_API_KEY', '')
    response = client.post('/api/patient/ask', json={'token': sign('patient', 'P001'),
        'datasource_id': 'regional_health', 'question': question})
    assert response.status_code == 200
    body = response.json()
    assert (body['feature'], body['query_scope'], body['interpretation_feature']) == (feature, scope, interpretation)
    if interpretation:
        assert body['smart_doctor']['audit_status'] == 'passed'
        assert body['smart_doctor']['data']['rows']
        assert body['smart_doctor']['report_id'] is not None
    if feature is None:
        assert body['smart_doctor']['data'] is None
        assert {c['feature'] for c in body['routing_choices']} == {'smart_doctor', 'query_console'}


def test_hospital_query_failure_keeps_feature_and_scope(client, monkeypatch):
    from tests.helpers import sign
    from backend import query_cache, llm_nl2sql
    monkeypatch.setattr(query_cache, 'lookup', lambda *a, **k: None)
    monkeypatch.setattr(llm_nl2sql, 'llm_available', lambda: False)
    body = client.post('/api/patient/ask', json={'token': sign('patient', 'P001'),
        'datasource_id': 'regional_health', 'question': '心内科有哪些医生'}).json()
    assert body['feature'] == 'query_console' and body['query_scope'] == 'hospital'
    assert body['query'] is None and body['error']


@pytest.mark.parametrize('question', ['我妈妈花费了多少', '我一般花多少钱', '我预计花费多少', '不要查我花费多少'])
def test_model_billing_label_cannot_override_subject_or_estimate(monkeypatch, question):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'billing', 'task': 'billing_query', 'scope': 'self', 'entities': [],
        'fields': [], 'confidence': .95, 'operations': ['personal_record'],
    }))
    assert not su.analyze(question).accepted


def test_model_limited_billing_offers_explicit_total_instead_of_silent_sum(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'billing', 'task': 'billing_query', 'scope': 'self', 'entities': [],
        'fields': [], 'confidence': .95, 'operations': ['personal_record'],
    }))
    result = su.analyze('我上次花费了多少')
    assert result.task == 'record_query' and result.record_kind == 'generic'
    assert decide('我上次花费了多少', subject_id='P001', understanding=result).feature == 'query_console'
