import json
from types import SimpleNamespace
from backend import config, llm_client, sourced_answers as sa, smart_doctor


def query(passed=True):
    return SimpleNamespace(admission=SimpleNamespace(passed=passed),
        result=SimpleNamespace(columns=['patient_id','record_id','test_name','result_value','unit','ref_range'],
            rows=[['P001','V0001-L','血糖','6.3','mmol/L','参考范围: 见报告']]),
        degradation=SimpleNamespace(level='L0'))


def test_model_receives_medical_results_and_knowledge_but_not_identity(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr(smart_doctor, '_retrieve_context', lambda *a, **k: [])
    prompts=[]
    def model(prompt, **kw):
        prompts.append(prompt)
        return json.dumps({'sufficient': False, 'missing': ['是否空腹'], 'sections': [
            {'text': '记录中的血糖为6.3 mmol/L，是否偏高需先确认空腹条件，并结合原报告参考范围判断。',
             'source_type': 'medical_interpretation', 'evidence_ids': ['result:1','kb:lab:血糖']} ]})
    monkeypatch.setattr(llm_client, 'call_llm', model)
    result=sa.review('我的血糖高吗，多少正常', query=query(), explain=True)
    assert '6.3' in prompts[0] and 'mmol/L' in prompts[0] and '3.9' in prompts[0]
    assert 'P001' not in prompts[0] and 'V0001-L' not in prompts[0]
    assert result['sections'][0]['source_type']=='medical_interpretation'
    assert '医院主库' in result['sections'][0]['source_name']
    assert sa.explain_linked_lab('我的血糖高吗，多少正常', query(), result)['sections']==result['sections']


def test_no_patient_result_can_be_recovered_from_blocked_query(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr(llm_client,'call_llm',lambda *a,**k: (_ for _ in ()).throw(AssertionError('拒绝后不能调用模型')))
    assert sa.review('解读我的报告', query=query(False), explain=True) is None


def test_missing_medical_conditions_are_not_reported_as_fully_covered(monkeypatch):
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr(smart_doctor, '_retrieve_context', lambda *a, **k: [])
    monkeypatch.setattr(llm_client, 'call_llm', lambda *a, **k: json.dumps({
        'sufficient': True, 'missing': ['是否空腹'], 'sections': []}))
    assert sa.review('血糖高吗', query=query(), explain=True)['sufficient'] is False
