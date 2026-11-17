import json
import sqlite3
import pytest
from backend import config, llm_client, llm_nl2sql, query_cache, semantic_understanding as su
from backend.patient_router import decide
from tests.test_api_patient import client, _ask
from tests.helpers import sign


@pytest.mark.parametrize('question', ['我是什么时候看的病', '我在哪个科室看的病', '我在哪个诊室看的病', '帮我查一下我的就诊医生'])
def test_own_visit_semantics_reaches_console(monkeypatch, question):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'fallback', 'task': 'record_query', 'scope': 'self', 'entities': [],
        'fields': [], 'confidence': .9, 'operations': ['personal_record']}))
    meaning = su.analyze(question)
    decision = decide(question, subject_id='P001', understanding=meaning)
    assert decision.feature == 'query_console' and decision.query_scope == 'self'
    assert meaning.record_kind == 'generic'


@pytest.mark.parametrize('patient', ['P001', 'P002'])
@pytest.mark.parametrize('question', ['我是什么时候看的病', '我上次的血糖是多少', '医生给我开的药怎么吃', '我看病花了多少钱'])
def test_unified_entry_reuses_original_console_translation_audit_and_result(client, monkeypatch, patient, question):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'intent': 'fallback', 'task': 'record_query', 'scope': 'self', 'entities': [],
        'fields': [], 'confidence': .9, 'operations': ['personal_record']}))
    monkeypatch.setattr(query_cache, 'lookup', lambda *a, **k: None)
    monkeypatch.setattr(query_cache, 'store', lambda *a, **k: None)
    calls = []
    def translate(question, datasource_id, **kw):
        calls.append((question, kw['subject_id']))
        return [{'id': 1, 'description': '本人就诊记录', 'sql':
                 f"SELECT visit_date, department FROM visits WHERE patient_id = '{kw['subject_id']}' ORDER BY visit_date DESC"}]
    monkeypatch.setattr(llm_nl2sql, 'decompose', translate)
    from backend.routers import query as original
    shared = original.run_direct_query
    used = []
    def original_console(req):
        used.append(req.question)
        return shared(req)
    monkeypatch.setattr(original, 'run_direct_query', original_console)
    token = sign('patient', patient)
    unified = _ask(client, token, question).json()
    assert used == [question], '本人查询必须调用原控制台实现'
    assert unified['feature'] == 'query_console' and unified['query_scope'] == 'self'
    direct = client.post('/api/query/direct', json={'token': token, 'datasource_id': 'regional_health', 'question': question}).json()
    assert unified['query']['result'] == direct['result']
    with sqlite3.connect(config.BUSINESS_DB) as db:
        expected = db.execute('SELECT visit_date, department FROM visits WHERE patient_id=? ORDER BY visit_date DESC', (patient,)).fetchall()
    assert unified['query']['result']['rows'] == [list(row) for row in expected]
    assert unified['query']['admission']['passed']
    assert all(subject == patient for _, subject in calls)


def test_unsafe_translated_plan_is_still_denied(client, monkeypatch):
    monkeypatch.setattr(query_cache, 'lookup', lambda *a, **k: [{'id': 1, 'description': '未绑定本人', 'sql': 'SELECT visit_date FROM visits'}])
    body = _ask(client, sign('patient', 'P001'), '我是什么时候看的病').json()
    assert body['query'] and not body['query']['admission']['passed']
    assert body['query']['result'] is None


def test_original_console_accepts_observed_missing_sql_label(client, monkeypatch):
    monkeypatch.setattr(llm_nl2sql, 'llm_available', lambda: True)
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k:
        "Sub question 1: 查询本人就诊日期\n```sql\nSELECT visit_date FROM visits WHERE patient_id = 'P001' ORDER BY visit_date;\n```")
    response = client.post('/api/query/direct', json={'token': sign('patient', 'P001'),
        'datasource_id': 'regional_health', 'question': '查看本人就诊日期（格式回归）'})
    assert response.status_code == 200
    assert response.json()['admission']['passed'] and response.json()['result']['rows']
