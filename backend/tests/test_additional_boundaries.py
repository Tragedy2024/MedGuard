import os
import sys
from pathlib import Path
os.environ['MEDGUARD_TESTING']='1'
import pytest
from backend import smart_doctor as sd, redflags
from backend.admission import admission_check


@pytest.mark.parametrize('q', ['我的血糖结果6.3正常吗', '我的血糖6.3正常吗',
                              '我的血糖正常范围是多少', '我的血糖参考范围是多少'])
def test_supplied_value_or_reference_question_never_reads_different_record(monkeypatch,q):
    def unexpected(*args,**kwargs):
        pytest.fail('Provided value or reference question must not use a different stored result')
    monkeypatch.setattr(sd,'fetch_personal_data',unexpected)
    out=sd.ask(q,'P001')
    assert out['data'] is None


@pytest.mark.parametrize('q', ['我没有胸痛，只想了解糖尿病', '目前没有呼吸困难', '无意识障碍'])
def test_explicitly_absent_red_flag_does_not_trigger_emergency(q):
    assert redflags.check(q) == ()


@pytest.mark.parametrize('q', ['没有胸痛，但是我呼吸困难', '原来没有胸痛，现在胸痛', '不是没有胸痛'])
def test_present_red_flag_is_not_suppressed(q):
    assert redflags.check(q)


@pytest.mark.parametrize('sql', [
    "SELECT * FROM clinical_records WHERE patient_id='P001' OR 1=1",
    "SELECT * FROM clinical_records WHERE patient_id='P001' OR patient_id='P002'",
    "SELECT * FROM clinical_records WHERE NOT(patient_id='P001')",
    "SELECT * FROM clinical_records WHERE EXISTS (SELECT 1 FROM patients WHERE patient_id='P001')",
])
def test_patient_binding_must_apply_to_returned_rows(sql):
    assert not admission_check(sql,'P001').passed


@pytest.mark.parametrize('sql', [
    "SELECT * FROM clinical_records WHERE patient_id='P001' AND (test_name='血糖' OR test_name='血脂')",
    "SELECT * FROM clinical_records WHERE patient_id='P001' OR patient_id='P001'",
])
def test_valid_bindings_remain_usable(sql):
    assert admission_check(sql,'P001').passed


def test_executor_cannot_mutate_a_database(tmp_path):
    import sqlite3
    from backend.routers.query import _execute
    path=tmp_path/'sandbox.db'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE sentinel (id INTEGER)')
    assert _execute('DROP TABLE sentinel', str(path)) is None
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='sentinel'").fetchone()


def test_each_joined_patient_source_must_be_restricted():
    assert not admission_check("SELECT b.name FROM patients a CROSS JOIN patients b WHERE a.patient_id='P001'",'P001').passed


def test_safe_medical_record_join_remains_available():
    assert admission_check("SELECT c.test_name FROM clinical_records c JOIN visits v ON c.visit_id=v.visit_id WHERE c.patient_id='P001'",'P001').passed


def test_unified_patient_router_agrees_with_public_answer():
    from backend.patient_router import decide
    assert decide('高血糖吃什么药', subject_id='P001').route == 'knowledge'


@pytest.mark.parametrize('name,unit', [('血糖','mmol/L'), ('血糖','mg/dL'), ('餐后血糖','mmol/L')])
def test_unqualified_or_wrong_unit_glucose_not_labeled_as_fasting(monkeypatch,name,unit):
    monkeypatch.setattr(sd,'fetch_personal_data',lambda plan,subject,**kwargs:
        sd.PersonalDataResult({'passed':True,'checked_tables':[]},
            {'level':'L0','label':'通过','message':''},
            ['test_name','result_value','unit','visit_date'],
            [[name,'6.3',unit,'2025-04-05']],plan[0]['sql']))
    out=sd.ask('我的血糖结果正常吗','P001')
    item=out['interpretation']['items'][0]
    assert item['result_value']=='6.3'
    assert '需确认' in item['label']
    assert '空腹血糖受损' not in item['label'] + item['text']


def test_explicit_fasting_glucose_can_use_matching_reference(monkeypatch):
    monkeypatch.setattr(sd,'fetch_personal_data',lambda plan,subject,**kwargs:
        sd.PersonalDataResult({'passed':True,'checked_tables':[]},
            {'level':'L0','label':'通过','message':''},
            ['test_name','result_value','unit','visit_date'],
            [['空腹血糖','6.3','mmol/L','2025-04-05']],plan[0]['sql']))
    out=sd.ask('我的血糖结果正常吗','P001')
    assert out['interpretation']['items'][0]['label']
    assert '需确认' not in out['interpretation']['items'][0]['label']


def test_route_badge_does_not_claim_record_access_for_unsupported_measurement():
    from backend.patient_router import decide
    assert decide('我的血压正常吗',subject_id='P001').route == 'clarify'


def test_public_guidance_route_has_matching_explanation():
    from backend.patient_router import decide
    route=decide('我肚子不舒服挂什么科室',subject_id='P001')
    assert route.route == 'knowledge'
    assert '公开' in route.reason


@pytest.mark.parametrize('role,sql', [
    ('patient', "SELECT * FROM clinical_records WHERE patient_id='P001' OR 1=1"),
    ('staff', 'DROP TABLE patients'),
])
def test_generated_unsafe_plan_is_stopped_in_real_api(tmp_path,monkeypatch,role,sql):
    import sqlite3
    from backend import config,llm_nl2sql,query_cache
    from tests.test_smart_doctor import _client
    from tests.helpers import sign
    client=_client(tmp_path,monkeypatch)
    monkeypatch.setattr(query_cache,'lookup',lambda *args,**kwargs:None)
    monkeypatch.setattr(query_cache,'store',lambda *args,**kwargs:None)
    monkeypatch.setattr(llm_nl2sql,'llm_available',lambda:True)
    monkeypatch.setattr(llm_nl2sql,'decompose',lambda *args,**kwargs:[{'id':0,'description':'合成边界测试','sql':sql}])
    response=client.post('/api/query/direct',json={
        'token':sign(role,'P001' if role=='patient' else 'S001'),
        'datasource_id':'regional_health','question':'合成测试问题'})
    assert response.status_code==200
    body=response.json()
    assert body['degradation']['level']=='L3'
    assert body['result'] is None and body['plan']==[]
    with sqlite3.connect(config.BUSINESS_DB) as con:
        assert con.execute('SELECT COUNT(*) FROM patients').fetchone()[0]==30
