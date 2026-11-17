import os
import sys
from pathlib import Path
os.environ['MEDGUARD_TESTING'] = '1'
import pytest
from backend import smart_doctor as sd, dataset_kb
from backend.schemas import SmartDoctorResponse


@pytest.fixture(autouse=True)
def no_external_or_personal_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Public guidance must not call a model or read personal records')
    monkeypatch.setattr(sd, '_call_llm', forbidden)
    monkeypatch.setattr(sd, 'fetch_personal_data', forbidden)


@pytest.mark.parametrize('q', ['你能告诉我血糖高如何治疗吗', '你能告诉我高血糖如何治疗吗',
                              '血糖偏高怎么办', '高血糖怎么治疗'])
def test_high_glucose_treatment_has_relevant_evidence(q):
    out = sd.ask(q, 'P001')
    SmartDoctorResponse.model_validate(out)
    assert out['interpretation']['items']
    assert '治疗' in out['interpretation']['title']
    assert any(i.get('ref_url') for i in out['interpretation']['items'])
    assert out['data'] is None
    assert out['evidence']['status'] == 'curated'


@pytest.mark.parametrize('q', ['高血糖吃什么药', '血糖高该用什么药'])
def test_drug_question_explains_classes_without_prescribing(q):
    out = sd.ask(q, 'P001')
    text = str(out['interpretation']) + str(out['advice'])
    assert '二甲双胍' in text and '胰岛素' in text
    assert '不能' in text and '自行' in text
    assert not any(w in text for w in ('每日一片', '每天一片', 'mg', '毫克'))


@pytest.mark.parametrize('q', ['我血糖不正常改挂什么科室', '血糖高挂什么科', '高血糖看哪个科'])
def test_glucose_department_does_not_require_disease_diagnosis(q):
    out = sd.ask(q, 'P001')
    assert any(i.get('ref_title') == '内分泌科' for i in out['interpretation']['items'])


@pytest.mark.parametrize('q', ['我肚子不舒服挂什么科室', '我肚子不舒服', '我肚子难受'])
def test_abdominal_discomfort_gives_specific_clarification(q):
    out = sd.ask(q, 'P001')
    text = str(out['interpretation']) + str(out['advice'])
    assert '部位' in text and '多久' in text
    assert '全科' in text
    assert '换个问法' not in text


def test_abdominal_emergency_is_not_routine_triage():
    out = sd.ask('我肚子不舒服，突然剧烈腹痛', 'P001')
    assert out['advice']['urgent'] is True
    assert '急诊' in out['advice']['text']


def test_medication_definition_is_not_a_dump_of_every_field():
    out = sd.ask('头孢呋辛是啥', 'P001')
    text = out['interpretation']['text'] + ' '.join(i.get('ref_excerpt', '') for i in out['interpretation']['items'])
    assert '抗生素' in text and '细菌' in text
    assert '停药一周内勿饮酒' not in text
    assert '医院审核' not in str(out)


def test_dataset_department_votes_do_not_prescribe_priority(monkeypatch):
    from backend.knowledge import public_guidance
    # 本用例专测图谱投票分支；有来源的症状补问优先路径由独立用例覆盖。
    monkeypatch.setattr(public_guidance, 'lookup', lambda *a, **k: None)
    monkeypatch.setattr(dataset_kb, 'lookup_symptom', lambda _: {
        'matched': '头晕', 'total': 4,
        'candidates': [{'department': '科室甲', 'disease_count': 3},
                       {'department': '科室乙', 'disease_count': 1}]})
    out = sd.ask('我头晕', 'P001')
    assert out['interpretation']['items']
    assert '优先咨询科室甲' not in out['advice']['text']
    assert '就诊顺序' in out['interpretation']['text']
