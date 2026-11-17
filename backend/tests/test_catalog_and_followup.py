import json

import pytest

from backend import config, llm_client, semantic_understanding as su, smart_doctor as sd


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    def install(name=None, kind='disease', task='disease_examples', fields=None):
        payload = json.dumps({'intent': kind, 'task': task, 'scope': 'public',
            'entities': [{'name': name, 'kind': kind, 'mention': name}] if name else [],
            'fields': fields if fields is not None else ['overview'], 'confidence': .9})
        monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: payload)
    install()
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('目录和症状补问不能查询患者记录'))
    return install


def test_unspecified_count_explains_real_coverage_and_offers_more(model):
    reply = sd.ask('了解几种疾病', 'P001')
    assert len(reply['interpretation']['items']) > 3
    assert reply['catalog']['overview_count'] > len(reply['interpretation']['items'])
    assert reply['catalog']['has_more']
    assert '简介' in reply['interpretation']['text']
    assert '先举3种' not in reply['interpretation']['text']


def test_explicit_ten_is_not_silently_capped_at_five(model):
    reply = sd.ask('请介绍10种疾病', 'P001')
    assert len(reply['interpretation']['items']) == 10
    assert reply['catalog']['requested_count'] == 10


def test_catalog_navigation_continues_instead_of_repeating_first_page(model):
    first = sd.ask('了解几种疾病', 'P001')
    context = {'intent': 'disease', 'task': 'disease_examples', 'personal': False,
               'catalog_offset': first['catalog']['next_offset'], 'catalog_limit': first['catalog']['requested_count']}
    second = sd.ask('继续查看疾病介绍', 'P001', context)
    names1 = {i['ref_title'] for i in first['interpretation']['items']}
    names2 = {i['ref_title'] for i in second['interpretation']['items']}
    assert names1.isdisjoint(names2)
    assert second['catalog']['offset'] == first['catalog']['returned_count']
    assert second['catalog']['overview_count'] == first['catalog']['overview_count']


def test_lung_cancer_has_sourced_overview(model):
    model('肺癌', task='overview')
    reply = sd.ask('我想了解一下肺癌', 'P001')
    assert reply['interpretation']['title'] == '肺癌的基本情况'
    assert all(i.get('ref_url', '').startswith('https://www.cancer.gov/') for i in reply['interpretation']['items'])
    assert reply['evidence']['status'] == 'curated'


def test_sore_throat_gets_initial_guidance_and_specific_questions(model):
    model('喉咙痛', 'symptom', 'triage', ['triage'])
    reply = sd.ask('我喉咙痛', 'P001')
    assert reply['interpretation']['items']
    assert '多久' in reply['interpretation']['text']
    assert reply['data'] is None


def test_gathering_symptom_details_is_not_missing_medical_evidence(model):
    model('头痛', 'symptom', 'clarify', [])
    reply = sd.ask('我的头痛', 'P001')
    assert reply['retrieval_plan']['strategy'] == 'clarify'
    assert reply['evidence']['status'] == 'not_applicable'
    assert reply['interpretation']['source'] == '对话补问'
    assert reply['advice'] is None


def test_catalog_followup_without_navigation_context_does_not_read_records(model):
    result = su.analyze('继续查看疾病介绍')
    assert result.task != 'record_query'


def test_swallowing_saliva_emergency_is_not_a_routine_followup(model):
    model('喉咙痛', 'symptom', 'triage', ['triage'])
    reply = sd.ask('喉咙痛，吞不下口水', 'P001')
    assert reply['advice']['urgent']


def test_ordinary_sore_throat_does_not_trigger_emergency(model):
    model('喉咙痛', 'symptom', 'triage', ['triage'])
    reply = sd.ask('我喉咙痛', 'P001')
    assert not reply['advice']['urgent']
