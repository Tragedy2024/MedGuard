import json
import sqlite3

import pytest

from backend import config, llm_client, semantic_understanding as su
from backend.patient_router import decide


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.db import init_db
    from tests.helpers import load_demo
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'METADATA_DB', str(tmp_path / 'meta.db'))
    monkeypatch.setattr(config, 'BUSINESS_DB', str(tmp_path / 'business.db'))
    init_db(config.METADATA_DB)
    result = TestClient(app)
    load_demo(result)
    return result


@pytest.mark.parametrize('question', ['我血糖正常吗', '我的血糖正常吗', '帮我看看我的血糖正不正常'])
def test_explicit_self_result_is_not_public_reference(monkeypatch, question):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'lab', 'task': 'reference', 'scope': 'self',
        'entities': [{'name': '血糖', 'kind': 'lab', 'mention': '血糖'}],
        'fields': ['reference'], 'confidence': .9, 'operations': ['knowledge'],
    }))
    result = su.analyze(question)
    assert decide(question, subject_id='P001', understanding=result).route == 'personal_record'


def test_explicit_reference_range_remains_public(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', False)
    result = su.analyze('我的血糖正常范围是多少')
    assert result.task != 'record_query'


@pytest.mark.parametrize('patient', ['P001', 'P002'])
def test_own_bill_total_is_bound_audited_and_matches_database(client, patient, monkeypatch):
    _mock_console_billing(monkeypatch)
    from tests.helpers import sign
    with sqlite3.connect(config.BUSINESS_DB) as db:
        expected = db.execute('SELECT ROUND(SUM(amount),2), COUNT(*) FROM billing WHERE patient_id=?', (patient,)).fetchone()
    response = client.post('/api/patient/ask', json={'token': sign('patient', patient),
        'datasource_id': 'regional_health', 'question': '我看病花了多少钱'})
    assert response.status_code == 200
    body = response.json()
    assert body['route'] == 'personal_record'
    assert body['smart_doctor'] is None
    reply = body['query']
    assert reply['admission']['passed']
    assert reply['result']['rows'][0][0] == expected[0]
    assert reply['result']['rows'][0][1] == expected[1]


def test_no_bill_records_is_not_reported_as_known_zero_lifetime_cost(client, monkeypatch):
    _mock_console_billing(monkeypatch)
    from tests.helpers import sign
    response = client.post('/api/patient/ask', json={'token': sign('patient', 'P999'),
        'datasource_id': 'regional_health', 'question': '我的看病账单合计多少钱'})
    reply = response.json()['query']
    assert reply['admission']['passed']
    assert reply['result']['rows'] == [[None, 0]]


def _mock_console_billing(monkeypatch):
    from backend import llm_nl2sql, query_cache
    monkeypatch.setattr(query_cache, 'lookup', lambda *a, **k: None)
    monkeypatch.setattr(query_cache, 'store', lambda *a, **k: None)
    monkeypatch.setattr(llm_nl2sql, 'llm_available', lambda: True)
    monkeypatch.setattr(llm_nl2sql, 'decompose', lambda q, ds, **kw: [
        {'id': 1, 'description': '本人账单合计', 'sql':
         f"SELECT ROUND(SUM(amount), 2), COUNT(*) FROM billing WHERE patient_id = '{kw['subject_id']}'"}])


def test_specific_billing_time_is_not_silently_replaced_with_all_records(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', False)
    result = su.analyze('我上次看病花了多少钱')
    assert result.task == 'record_query'
    assert result.record_kind == 'generic'
    assert su.plan_for(result).strategy == 'personal_sql'


def test_family_cost_cannot_be_rebound_to_self(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'billing', 'task': 'billing_query', 'scope': 'self', 'entities': [],
        'fields': [], 'confidence': .95,
    }))
    result = su.analyze('我妈妈看病花了多少钱')
    assert not result.accepted


def test_public_cost_estimate_is_not_treated_as_an_existing_bill(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', False)
    result = su.analyze('我看病一般要花多少钱')
    assert result.task != 'billing_query'
