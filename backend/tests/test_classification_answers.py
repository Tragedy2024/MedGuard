import os
import sys
from pathlib import Path

os.environ['MEDGUARD_TESTING'] = '1'
import pytest
from backend import smart_doctor as sd, dataset_kb
from backend.schemas import SmartDoctorResponse


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(dataset_kb, '_CACHE', {
        dataset_kb.DISEASE_FILE: {'diseases': {}},
        dataset_kb.SYMPTOM_FILE: {'symptoms': {}},
    })
    def forbidden(*args, **kwargs):
        raise AssertionError('Classification must not use models or patient records')
    monkeypatch.setattr(sd, '_call_llm', forbidden)
    monkeypatch.setattr(sd, 'fetch_personal_data', forbidden)


@pytest.mark.parametrize('q', ['糖尿病有几种', '糖尿病分几型', '糖尿病有哪些类型',
                                '请问糖尿病有哪几种？', '有哪几种糖尿病'])
def test_diabetes_classification_answers_the_question(q):
    out = sd.ask(q, 'P001')
    SmartDoctorResponse.model_validate(out)
    titles = [i['ref_title'] for i in out['interpretation']['items']]
    assert {'1型糖尿病', '2型糖尿病', '妊娠糖尿病'} <= set(titles)
    assert '常见症状' not in titles
    assert out['evidence']['model_used'] is False
    assert out['evidence']['status'] == 'curated'
    assert all(i['ref_url'].startswith('https://www.niddk.nih.gov/')
               for i in out['interpretation']['items'])
    assert '医院审核' not in str(out)


@pytest.mark.parametrize('q', ['有哪几种癌症', '癌症有哪些', '癌症分几类',
                                '常见癌症有哪些', '恶性肿瘤有哪些类型'])
def test_cancer_classification_has_examples_and_scope(q):
    out = sd.ask(q, 'P001')
    SmartDoctorResponse.model_validate(out)
    text = str(out['interpretation'])
    assert '肺癌' in text and '乳腺癌' in text and '白血病' in text
    assert '不是全部' in text
    assert '发生部位' in text and '组织' in text
    assert all(i['ref_url'].startswith('https://www.cancer.gov/')
               for i in out['interpretation']['items'])
    assert out['data'] is None


@pytest.mark.parametrize('q', ['糖尿病有几种药', '糖尿病有哪些症状', '2型糖尿病有几种',
                                '肺癌有几种', '我是哪种糖尿病', '糖尿病有哪些并发症',
                                '糖尿病有几种，癌症有几种'])
def test_public_classification_does_not_override_other_tasks(q):
    from backend.knowledge.classifications import lookup
    assert lookup(q) is None


def test_classification_followup_is_bound_to_topic():
    from backend.knowledge.classifications import lookup
    assert lookup('那有几种呢', {'intent': 'disease', 'entity': '糖尿病'})['entity'] == '糖尿病'
    assert lookup('肺癌有几种', {'intent': 'disease', 'entity': '糖尿病'}) is None


def test_public_classification_skips_optional_external_router(monkeypatch):
    monkeypatch.setattr(sd.jev_client, 'is_approved', lambda q: True)
    def forbidden(*args, **kwargs):
        raise AssertionError('Public classification should not need external routing')
    monkeypatch.setattr(sd.jev_client, 'classify', forbidden)
    assert sd.ask('癌症有哪些', 'P001')['interpretation']['items']
