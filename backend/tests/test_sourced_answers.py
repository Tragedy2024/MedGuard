import json
from backend import config, llm_client, sourced_answers as sa
from backend.schemas import QueryResponse


def enable(monkeypatch, payload):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'test-key')
    calls = []
    def model(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(llm_client, 'call_llm', model)
    return calls


def test_classifier_runs_for_clear_template_question(monkeypatch):
    enable(monkeypatch, {'feature': 'query_console', 'scope': 'self', 'explain': True, 'confidence': .95})
    result = sa.classify('帮我看懂我的检查报告')
    assert result.feature == 'query_console' and result.explain


def test_classifier_failure_uses_explicit_fallback(monkeypatch):
    enable(monkeypatch, {'feature': 'query_console', 'scope': None, 'confidence': .4})
    assert sa.classify('这个是什么') is None


def test_ai_supplement_is_labeled_and_not_given_fake_citations(monkeypatch):
    enable(monkeypatch, {'sufficient': False, 'missing': ['缺少疾病简介'], 'sections': [
        {'text': '可以先了解这种疾病涉及的器官及常见表现，具体病情需要医生判断。', 'source_type': 'ai_supplement', 'evidence_ids': []}]})
    result = sa.review('这个疾病是什么')
    assert result['status'] == 'reviewed' and result['sufficient'] is False
    assert '未经资料核验' in result['sections'][0]['source_name']


def test_fake_citation_and_unsupported_dose_are_discarded(monkeypatch):
    enable(monkeypatch, {'sufficient': False, 'missing': [], 'sections': [
        {'text': '伪造的资料结论', 'source_type': 'knowledge', 'evidence_ids': ['fake-id']},
        {'text': '每天服用2片', 'source_type': 'ai_supplement', 'evidence_ids': []}]})
    assert sa.review('怎么理解这个病')['sections'] == []


def test_review_model_failure_never_hides_original_answer(monkeypatch):
    enable(monkeypatch, {'unexpected': True})
    result = sa.review('肺炎是什么')
    assert result['status'] == 'unavailable' and not result['sections']


def test_query_review_never_receives_record_values_sql_or_token(monkeypatch):
    calls = enable(monkeypatch, {'sufficient': True, 'missing': [], 'sections': []})
    query = QueryResponse.model_validate({'question': '我的血糖是多少',
        'admission': {'passed': True, 'checked_tables': []}, 'plan': [], 'events': [],
        'rewrite': {'applied': 0, 'log': []}, 'degradation': {'level': 'L0', 'label': '通过', 'message': ''},
        'metrics': {'elapsed_ms': 1, 'llm_calls': 0, 'db_access': 0},
        'result': {'columns': ['result_value', 'unit'], 'rows': [['private-marker-9876', 'mmol/L']]}})
    sa.review('我的血糖是多少', query=query)
    assert 'result_value' in calls[0] and 'private-marker-9876' not in calls[0]
    assert 'mmol/L' not in calls[0]


def test_blocked_query_cannot_trigger_supplement(monkeypatch):
    calls = enable(monkeypatch, {'sufficient': False, 'missing': [], 'sections': []})
    from types import SimpleNamespace
    query = SimpleNamespace(admission=SimpleNamespace(passed=False), result=None)
    assert sa.review('别人的报告', query=query, explain=True) is None
    assert calls == []


def test_review_contains_main_answer_and_partial_evidence(monkeypatch):
    calls = enable(monkeypatch, {'sufficient': True, 'missing': [], 'sections': []})
    reply = {'evidence': {'status': 'curated'}, 'interpretation': {'source': '真实资料来源',
        'title': '简介', 'text': '这是正文中的定义', 'items': [
            {'ref_id': 'symptoms', 'ref_source': '症状资料', 'ref_excerpt': '这是症状字段'}]}}
    sa.review('是什么病', reply=reply)
    assert '这是正文中的定义' in calls[0] and '这是症状字段' in calls[0]


def test_query_review_does_not_claim_missing_records_or_request_resubmission(monkeypatch):
    enable(monkeypatch, {'sufficient': False, 'missing': ['没有提供实际报告'], 'sections': [
        {'text': '请提供你的检查报告', 'source_type': 'ai_supplement', 'evidence_ids': []}]})
    from types import SimpleNamespace
    from backend import smart_doctor
    monkeypatch.setattr(smart_doctor, '_retrieve_context', lambda *a, **k: [])
    query = SimpleNamespace(admission=SimpleNamespace(passed=True),
        result=SimpleNamespace(columns=['result_value'], rows=[['private']]),
        degradation=SimpleNamespace(level='L0'))
    result = sa.review('帮我看懂报告', query=query, explain=True)
    assert result['sections'] == []
    assert '医学判断' in result['missing'][0] and '没有提供' not in result['missing'][0]
