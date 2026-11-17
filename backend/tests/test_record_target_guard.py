import os
import sys
from pathlib import Path
os.environ['MEDGUARD_TESTING'] = '1'
import pytest
from backend import smart_doctor as sd
from backend.schemas import SmartDoctorResponse


@pytest.fixture
def calls(monkeypatch):
    calls = []
    def fetch(plan, subject, **kwargs):
        calls.append(plan)
        return sd.PersonalDataResult({'passed': True, 'checked_tables': []}, {'level': 'L0', 'label': '通过', 'message': ''},
            ['检验项目', '检验结果', '单位', '就诊日期'],
            [['血糖', '6.3', 'mmol/L', '2025-04-05']], plan[0]['sql'])
    monkeypatch.setattr(sd, 'fetch_personal_data', fetch)
    return calls


@pytest.mark.parametrize('q', ['我的血压正常吗', '我的心率正常吗', '我的体温正常吗',
                              '我的血压报告正常吗', '我的甲胎蛋白正常吗',
                              '我的血压和血糖正常吗'])
@pytest.mark.parametrize('context', [None, {'intent': 'lab', 'entity': '血糖', 'personal': True}])
def test_unrecognized_explicit_target_never_reads_latest_glucose(calls, q, context):
    out = sd.ask(q, 'P001', context=context)
    SmartDoctorResponse.model_validate(out)
    assert calls == [], 'Unknown measurement was replaced by an unfiltered latest lab query'
    assert out['data'] is None
    assert out['evidence']['status'] == 'insufficient'
    assert not out['interpretation']['items']


def test_known_glucose_remains_filtered(calls):
    out = sd.ask('我的血糖正常吗', 'P001')
    assert calls and "c.test_name IN ('血糖')" in calls[0][0]['sql']
    assert out['data']['rows'][0][0] == '血糖'


@pytest.mark.parametrize('q', ['我的检验报告正常吗', '我的检查报告正常吗', '我上次的化验结果正常吗'])
def test_explicit_generic_reports_remain_available(calls, q):
    assert sd.ask(q, 'P001')['data']['rows']
    assert calls


def test_true_followup_keeps_verified_personal_target(calls):
    out = sd.ask('那正常吗', 'P001', context={'intent': 'lab', 'entity': '血糖', 'personal': True})
    assert calls and "c.test_name IN ('血糖')" in calls[0][0]['sql']
    assert out['data']['rows']
