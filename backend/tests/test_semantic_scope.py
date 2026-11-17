import json

import pytest

from backend import config, llm_client, semantic_understanding as su, smart_doctor as sd


@pytest.fixture
def install(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('公开知识不应读取记录'))
    def fake(task='disease_examples', entities=None, fields=None):
        payload = json.dumps({'intent': 'disease', 'task': task, 'scope': 'public',
            'entities': entities or [], 'fields': fields or ['overview'], 'confidence': .95})
        monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: payload)
    return fake


@pytest.mark.parametrize('question', ['你知道哪几种癌症', '能举几个恶性肿瘤的例子吗', '请介绍几种糖尿病'])
def test_named_disease_scope_cannot_be_discarded_by_generic_examples(install, question):
    install()
    reply = sd.ask(question, 'P001')
    expected = '糖尿病' if '糖尿病' in question else '癌症'
    assert reply['understanding']['entities'] == [expected]
    assert reply['understanding']['task'] == 'classification'
    assert reply['interpretation']['title'] == expected + '的分类'
    assert not any(i['ref_title'] in {'高血压', '高血脂'} for i in reply['interpretation']['items'])
    assert all(i.get('ref_url', '').startswith('https://') for i in reply['interpretation']['items'])


def test_unspecified_disease_examples_still_work(install):
    install()
    reply = sd.ask('你能说明几种病', 'P001')
    assert len(reply['interpretation']['items']) == 12
    assert reply['understanding']['task'] == 'disease_examples'


def test_specific_unknown_disease_is_not_replaced_with_general_examples(install):
    install()
    understood = su.analyze('请介绍几种高血压')
    assert not understood.accepted
    assert understood.status == 'invalid'


def test_unavailable_disease_is_insufficient_not_curated(install, monkeypatch):
    install('overview', [{'name': '外耳道多毛', 'kind': 'disease', 'mention': '外耳道多毛'}])
    monkeypatch.setattr(sd.dataset_kb, 'lookup_disease', lambda *a, **k: None)
    monkeypatch.setattr(sd, '_retrieve_context', lambda *a, **k: [])
    reply = sd.ask('你知道外耳道多毛是啥病吗', 'P001')
    assert reply['understanding']['status'] == 'model'
    assert reply['evidence']['status'] == 'insufficient'
    assert '糖尿病' not in reply['interpretation']['text']
    assert '外耳道多毛' in reply['interpretation']['text']
    assert not reply['evidence']['model_used']


def test_existing_disease_overview_is_not_lost_to_missing_graph_overview(install, monkeypatch):
    install('overview', [{'name': '糖尿病', 'kind': 'disease', 'mention': '糖尿病'}])
    monkeypatch.setattr(sd.dataset_kb, 'lookup_disease', lambda *a, **k: {'matched': '糖尿病', 'facts': {'dept': '内分泌科'}})
    reply = sd.ask('你知道糖尿病是啥病吗', 'P001')
    assert reply['interpretation']['items']
    assert reply['evidence']['status'] == 'curated'
    assert reply['retrieval_plan']['filters']['fields'] == ['overview']


def test_named_category_is_in_semantic_candidates(install):
    candidates = su._known_entities()
    assert any(doc.name == '癌症' and '恶性肿瘤' in doc.aliases for doc in candidates)


@pytest.mark.parametrize('question', ['请介绍几种传染病', '你知道哪几种血液病'])
def test_unrecognized_category_must_not_become_arbitrary_diseases(install, question):
    install()
    result = su.analyze(question)
    assert result.status == 'invalid'
