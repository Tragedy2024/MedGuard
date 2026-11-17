import pytest
from backend import sourced_answers as sa
from tests.test_api_patient import client, _ask
from tests.helpers import sign


@pytest.mark.parametrize('question', ['我的血糖高吗，多少是正常的', '我的血糖正常吗，正常范围是多少'])
def test_linked_request_reads_then_explains_without_mode_choice(client, question):
    from backend import config
    # Seeded cache plan is identical to the original console's own-record query.
    from backend import query_cache
    original_lookup = query_cache.lookup
    with pytest.MonkeyPatch.context() as m:
        m.setattr(query_cache, 'lookup', lambda q, *a, **k: original_lookup('我上次的血糖是多少', *a, **k))
        body = _ask(client, sign('patient', 'P001'), question).json()
    assert body['feature'] == 'query_console' and body['query_scope'] == 'self'
    assert body['query']['admission']['passed']
    assert body['routing_choices'] == []
    review = body['answer_review']
    assert review and review['sections']
    text = ' '.join(s['text'] for s in review['sections'])
    assert '6.3' in text and '3.9' in text and '空腹' in text
    assert '不能' in text or '无法' in text
    assert {'医院主库', '项目整理知识库（未经临床复核）'} <= {s['source_name'] for s in review['sections']}


def test_two_unrelated_tasks_are_not_forced_into_linked_lab():
    assert sa.linked_lab_request('我想了解肺癌，再查我的血糖') is None
    assert sa.linked_lab_request('我妈妈的血糖高吗，多少正常') is None
    assert sa.linked_lab_request('我的血糖8正常吗') is None


def test_model_low_confidence_does_not_split_one_linked_goal(client, monkeypatch):
    from backend import config, llm_client, query_cache
    import json
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps(
        {'feature': 'smart_doctor', 'scope': None, 'confidence': .4, 'explain': True}))
    lookup = query_cache.lookup
    monkeypatch.setattr(query_cache, 'lookup', lambda q, *a, **k: lookup('我上次的血糖是多少', *a, **k))
    body = _ask(client, sign('patient', 'P001'), '我的血糖高吗，多少是正常的').json()
    assert body['query']['admission']['passed'] and body['routing_choices'] == []
    assert any(s['source_type'] == 'database' for s in body['answer_review']['sections'])
