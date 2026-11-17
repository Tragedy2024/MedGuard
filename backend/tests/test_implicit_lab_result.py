import os
import sys
from pathlib import Path
os.environ['MEDGUARD_TESTING'] = '1'
import pytest
from backend import smart_doctor as sd
from backend.schemas import SmartDoctorResponse


@pytest.fixture
def calls(monkeypatch):
    seen = []
    def fetch(plan, subject, **kwargs):
        seen.append((plan, subject, kwargs))
        return sd.PersonalDataResult({'passed': True, 'checked_tables': []},
            {'level': 'L0', 'label': '通过', 'message': ''},
            ['检验项目', '检验结果', '单位', '就诊日期'],
            [['血糖', '6.3', 'mmol/L', '2025-04-05']], plan[0]['sql'])
    monkeypatch.setattr(sd, 'fetch_personal_data', fetch)
    return seen


@pytest.mark.parametrize('q', ['血糖结果正常吗', '血糖检查结果是否正常', '血糖报告正常么',
                              '血糖结果正不正常', '血糖结果偏高吗'])
def test_result_judgment_uses_bound_record(calls, q):
    out = sd.ask(q, 'P001')
    SmartDoctorResponse.model_validate(out)
    assert calls and calls[0][1] == 'P001'
    assert "c.test_name IN ('血糖')" in calls[0][0][0]['sql']
    assert out['retrieval_plan']['strategy'] == 'personal_sql'
    assert out['data']['rows'][0][1] == '6.3'
    items = out['interpretation']['items']
    assert len(items) == 1 and items[0]['result_value'] == '6.3'
    assert 'label' in items[0]


@pytest.mark.parametrize('q', ['血糖的正常范围是多少', '血糖检查有什么用',
                              '血糖结果通常正常吗', '我妈妈的血糖结果正常吗',
                              '他的血糖结果正常吗', '患者的血糖结果正常吗',
                              '血糖结果6.3正常吗', '假如血糖结果正常吗'])
def test_public_or_supplied_values_do_not_read_own_records(calls, q):
    sd.ask(q, 'P001')
    assert calls == []


def test_no_result_does_not_turn_into_reference_card(calls, monkeypatch):
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda plan, subject, **kwargs:
        sd.PersonalDataResult({'passed': True, 'checked_tables': []},
            {'level': 'L0', 'label': '通过', 'message': ''}, [], [], plan[0]['sql']))
    out = sd.ask('血糖结果正常吗', 'P001')
    assert out['data']['rows'] == []
    assert out['interpretation']['items'] == []
    assert '未找到' in out['interpretation']['title']


def test_result_question_through_real_audited_api(tmp_path, monkeypatch):
    from tests.test_smart_doctor import _client
    from tests.helpers import sign
    client = _client(tmp_path, monkeypatch)
    response = client.post('/api/smart-doctor/ask', json={
        'token': sign('patient', 'P001'), 'datasource_id': 'regional_health',
        'question': '血糖结果正常吗'})
    assert response.status_code == 200
    body = response.json()
    assert body['retrieval_plan']['strategy'] == 'personal_sql'
    assert body['admission']['passed'] is True
    assert body['audit_status'] == 'passed'
    assert body['data']['rows']
    assert body['data']['rows'][0][0] == '血糖'
    assert body['interpretation']['items'][0]['result_value'] == str(body['data']['rows'][0][1])
    assert body['report_id'] is not None
