import json
import time

import pytest

from backend import config, llm_client, smart_doctor as sd


def meaning(task='management', entities=None, fields=None, **changes):
    data = {
        'intent': 'lab', 'task': task, 'scope': 'public',
        'entities': entities or [{'name': '血糖', 'kind': 'lab', 'mention': '血糖'}],
        'fields': fields if fields is not None else ['treatments'],
        'confidence': 0.96,
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def semantic(monkeypatch):
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(config, 'SEMANTIC_ENABLED', True, raising=False)
    monkeypatch.setattr(config, 'SEMANTIC_TIMEOUT_SEC', 1.0, raising=False)
    calls = []

    def install(payload):
        def fake(prompt, **kwargs):
            calls.append(prompt)
            return payload
        monkeypatch.setattr(llm_client, 'call_llm', fake)
    return calls, install


def test_management_request_uses_meaning_not_indicator_overview(semantic, monkeypatch):
    calls, install = semantic
    install(meaning())
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('公开管理问题不能读本人记录'))
    result = sd.ask('血糖偏高应该怎么控制', 'P001')
    assert result['interpretation']['title'] == '血糖升高的一般处理与治疗'
    assert result['retrieval_plan']['filters']['fields'] == ['treatments']
    assert result['understanding']['status'] == 'model'
    assert len(calls) == 1
    assert result['data'] is None


def test_multiple_symptoms_and_relationship_are_not_replaced_by_department_card(semantic, monkeypatch):
    calls, install = semantic
    install(meaning('relationship', [
        {'name': '头晕', 'kind': 'symptom', 'mention': '头晕'},
        {'name': '恶心', 'kind': 'symptom', 'mention': '恶心'},
        {'name': '血压', 'kind': 'lab', 'mention': '血压'},
    ], ['relationships'], intent='symptom'))
    monkeypatch.setattr(sd, '_retrieve_context', lambda *a, **k: [])
    result = sd.ask('头晕还有点恶心，是不是血压有问题', 'P001')
    assert result['retrieval_plan']['strategy'] == 'hybrid'
    assert result['understanding']['entities'] == ['头晕', '恶心', '血压']
    assert result['understanding']['task'] == 'relationship'
    assert '关系' in result['understanding']['summary']
    assert result['interpretation']['title'] != '关于「头晕」的就诊科室'
    assert result['evidence']['status'] == 'insufficient'
    assert len(calls) == 1


@pytest.mark.parametrize('question', ['糖尿病挂什么科', '我上次的血糖是多少'])
def test_exact_fast_paths_do_not_call_semantic_model(semantic, question):
    from backend import semantic_understanding as su
    _, install = semantic
    install('not JSON: should never be used')
    result = su.analyze(question)
    assert result.status == 'rule'
    assert not result.attempted


def test_negated_indicator_does_not_become_personal_record(semantic, monkeypatch):
    _, install = semantic
    install(meaning('reference', [
        {'name': '血糖', 'kind': 'lab', 'mention': '血糖', 'negated': True},
        {'name': '血压', 'kind': 'lab', 'mention': '血压'},
    ], ['reference']))
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('否定语句不能变成取本人血糖'))
    monkeypatch.setattr(sd, '_retrieve_context', lambda *a, **k: [])
    result = sd.ask('不要查我的血糖，我想了解血压的正常范围', 'P001')
    assert result['understanding']['entities'] == ['血压']
    assert result['data'] is None


@pytest.mark.parametrize('payload', [
    'not JSON',
    meaning(confidence=0.1),
    meaning(sql='SELECT * FROM patients'),
    meaning(patient_id='P002'),
    meaning(entities=[{'name': '糖尿病', 'kind': 'disease', 'mention': '血糖'}]),
])
def test_invalid_or_invented_meaning_is_rejected(semantic, payload):
    from backend import semantic_understanding as su
    _, install = semantic
    install(payload)
    result = su.analyze('血糖偏高应该怎么控制')
    assert result.status == 'invalid'
    assert not result.accepted


def test_sensitive_identity_is_redacted_before_model(semantic):
    from backend import semantic_understanding as su
    calls, install = semantic
    install(meaning())
    su.analyze('我叫张三，身份证110101199001011234，手机13800138000，血糖偏高应该怎么控制')
    assert calls
    assert all(s not in calls[0] for s in ['张三', '110101199001011234', '13800138000'])


def test_timeout_is_bounded_and_visible(semantic, monkeypatch):
    from backend import semantic_understanding as su
    monkeypatch.setattr(config, 'SEMANTIC_TIMEOUT_SEC', 0.02)
    def slow(*a, **k):
        time.sleep(0.1)
        return meaning()
    monkeypatch.setattr(llm_client, 'call_llm', slow)
    start = time.monotonic()
    result = su.analyze('血糖偏高应该怎么控制')
    assert result.status == 'timeout'
    assert time.monotonic() - start < 0.09
    time.sleep(0.11)


def test_followup_uses_only_topic_context(semantic):
    from backend import semantic_understanding as su
    calls, install = semantic
    install(meaning('caution', [{'name': '头孢呋辛', 'kind': 'medication', 'mention': '它'}], ['caution'], intent='medication'))
    result = su.analyze('它会有什么副作用', context={'intent': 'medication', 'entity': '头孢呋辛', 'personal': False, 'patient_name': '张三', 'rows': [['秘密病历']]})
    assert result.accepted
    assert result.entities[0].name == '头孢呋辛'
    assert '头孢呋辛' in calls[0]
    assert '秘密病历' not in calls[0] and '张三' not in calls[0]


def test_patient_api_reuses_one_semantic_result(semantic, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.db import init_db
    from tests.helpers import load_demo, sign
    calls, install = semantic
    install(meaning())
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'METADATA_DB', str(tmp_path / 'meta.db'))
    monkeypatch.setattr(config, 'BUSINESS_DB', str(tmp_path / 'business.db'))
    init_db(config.METADATA_DB)
    client = TestClient(app)
    load_demo(client)
    response = client.post('/api/patient/ask', json={'token': sign('patient', 'P001'), 'datasource_id': 'regional_health', 'question': '血糖偏高应该怎么控制'})
    assert response.status_code == 200
    body = response.json()
    assert body['route'] == 'knowledge'
    assert body['smart_doctor']['understanding']['status'] == 'model'
    assert body['smart_doctor']['interpretation']['title'] == '血糖升高的一般处理与治疗'
    assert len(calls) == 1


def test_model_cannot_turn_other_person_request_into_self_access(semantic, monkeypatch):
    _, install = semantic
    install(meaning('record_query', scope='self', fields=[]))
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('不能把他人查询改成本人查询'))
    result = sd.ask('帮我看我妈妈上次的血糖结果', 'P001')
    assert result['data'] is None
    assert result['retrieval_plan']['strategy'] != 'personal_sql'


@pytest.mark.parametrize('task,fields', [('triage', ['triage']), ('management', ['treatments']), ('department', ['department'])])
def test_colloquial_discomfort_uses_existing_public_topic(semantic, task, fields):
    _, install = semantic
    install(meaning(task, [{'name': '腹部不适', 'kind': 'symptom', 'mention': '肚子不舒服'}], fields, intent='symptom'))
    result = sd.ask('我肚子不舒服该怎么办', 'P001')
    assert result['understanding']['status'] == 'model'
    assert result['interpretation']['title'] == '先了解腹部不适的具体情况'
    assert result['data'] is None


def test_model_cannot_turn_self_care_question_into_record_read(semantic, monkeypatch):
    _, install = semantic
    install(meaning('record_query', scope='self', fields=[]))
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('一般处理问题不应读本人记录'))
    result = sd.ask('我的血糖高应该怎么控制', 'P001')
    assert result['data'] is None
    assert result['retrieval_plan']['strategy'] == 'clarify'


def test_two_public_fields_are_both_answered_without_generation(semantic, monkeypatch):
    calls, install = semantic
    install(meaning('classification', [{'name': '糖尿病', 'kind': 'disease', 'mention': '糖尿病'}],
                    ['classification', 'department'], intent='disease'))
    monkeypatch.setattr(sd.dataset_kb, 'lookup_disease', lambda *a, **k: {'facts': {'departments': ['内分泌科']}})
    result = sd.ask('糖尿病有哪些类型，又该去哪个科看', 'P001')
    assert result['retrieval_plan']['filters']['fields'] == ['classification', 'department']
    assert '分类' in result['understanding']['summary'] and '科室' in result['understanding']['summary']
    kinds = [item['ref_kind'] for item in result['interpretation']['items']]
    assert any('分类' in k for k in kinds) and any('科室' in k for k in kinds)
    assert len(calls) == 1


def test_semantic_failure_does_not_silently_return_first_keyword(semantic):
    _, install = semantic
    install('not json')
    result = sd.ask('头晕还有恶心，是不是血压有问题', 'P001')
    assert result['understanding']['status'] == 'invalid'
    assert result['retrieval_plan']['strategy'] == 'clarify'
    assert result['evidence']['status'] == 'insufficient'
    assert result['data'] is None


def test_symptom_display_name_maps_to_retrieval_entity_id(semantic):
    from backend import semantic_understanding as su
    from backend.knowledge.corpus import build_docs
    _, install = semantic
    # 使用仍走原结构化科室文档的症状；头晕已有独立公开资料，现走 triage。
    install(meaning('department', [{'name': '咳嗽', 'kind': 'symptom', 'mention': '咳嗽'}],
                    ['department'], intent='symptom'))
    understood = su.analyze('能帮我说说咳嗽通常可以找哪个科看吗')
    plan = su.plan_for(understood)
    assert any(plan.filters.matches(doc) for doc in build_docs(sd.load_knowledge()) if doc.name == '咳嗽')


def test_symptom_cluster_does_not_rename_headache_as_dizziness(semantic):
    from backend import semantic_understanding as su
    _, install = semantic
    install(meaning('triage', [{'name': '头痛', 'kind': 'symptom', 'mention': '头痛'}], ['triage'], intent='symptom'))
    result = su.analyze('我最近有点头痛，应该怎么办才好')
    assert result.accepted
    assert result.entities[0].name == '头痛'


def test_unsupported_self_indicator_never_reads_nearest_lab_record(semantic, monkeypatch):
    _, install = semantic
    install(meaning('record_query', [{'name': '血压', 'kind': 'lab', 'mention': '血压'}], [], scope='self'))
    monkeypatch.setattr(sd, 'fetch_personal_data', lambda *a, **k: pytest.fail('未支持的血压不能读取血糖记录'))
    result = sd.ask('能帮我翻翻我之前的血压结果吗', 'P001')
    assert result['retrieval_plan']['strategy'] == 'clarify'
    assert result['data'] is None


def test_negated_lab_is_not_reintroduced_by_raw_keyword_matching(semantic, monkeypatch):
    _, install = semantic
    install(meaning('record_query', [
        {'name': '血糖', 'kind': 'lab', 'mention': '血糖', 'negated': True},
        {'name': '血脂', 'kind': 'lab', 'mention': '血脂'},
    ], [], scope='self'))
    captured = []
    def fetch(plan, *args, **kwargs):
        captured.extend(plan)
        return sd.PersonalDataResult(
            {'passed': True, 'checked_tables': [], 'bound_to_subject': True},
            {'level': 'L0', 'label': '通过', 'message': '', 'message_cn': ''}, [], [], '', None, '')
    monkeypatch.setattr(sd, 'fetch_personal_data', fetch)
    result = sd.ask('不要查我的血糖，帮我看上次的血脂结果', 'P001')
    assert result['understanding']['entities'] == ['血脂']
    assert '血脂' in json.dumps(captured, ensure_ascii=False)
    assert '血糖' not in json.dumps(captured, ensure_ascii=False)


def test_busy_semantic_service_does_not_start_unbounded_calls(semantic):
    from backend import semantic_understanding as su
    calls, install = semantic
    install(meaning())
    assert su._SLOTS.acquire(blocking=False) and su._SLOTS.acquire(blocking=False)
    try:
        result = su.analyze('血糖偏高应该怎么控制')
        assert result.status == 'unavailable'
        assert not calls
    finally:
        su._SLOTS.release()
        su._SLOTS.release()


@pytest.mark.parametrize('base,structured,thinking', [
    ('https://api.deepseek.com/v1', True, True),
    ('https://compatible.example/v1', True, False),
    ('https://api.deepseek.com/v1', False, False),
])
def test_structured_call_uses_bounded_output_and_provider_specific_options(monkeypatch, base, structured, thinking):
    from types import SimpleNamespace
    import openai
    constructor, requests = [], []
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
    def client(**kwargs):
        constructor.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai, 'OpenAI', client)
    monkeypatch.setattr(config, 'LLM_API_KEY', 'offline-test-key')
    monkeypatch.setattr(config, 'LLM_API_BASE', base)
    monkeypatch.setattr(config, 'LLM_MODEL', 'deepseek-flash')
    llm_client.call_llm('输出JSON', structured=structured)
    assert constructor[0]['max_retries'] == (0 if structured else 1)
    assert ('extra_body' in requests[0]) == thinking
    assert ('response_format' in requests[0]) == structured
