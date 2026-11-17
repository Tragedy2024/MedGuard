from backend.knowledge.grounding import validate_grounded_payload
from backend import smart_doctor


EVIDENCE = [{
    "ref_id": "drug:1",
    "ref_title": "阿托伐他汀片",
    "ref_excerpt": "阿托伐他汀片每片 20mg。具体用法应遵医嘱。",
}]


def test_grounded_claim_requires_real_evidence_id():
    payload = {
        "answer_mode": "grounded",
        "claims": [{"text": "具体用法应遵医嘱。", "evidence_ids": ["missing"]}],
    }
    assert any("不存在" in item for item in validate_grounded_payload(payload, EVIDENCE))


def test_grounded_claim_rejects_number_not_in_evidence():
    payload = {
        "answer_mode": "grounded",
        "claims": [{"text": "每片 40mg。", "evidence_ids": ["drug:1"]}],
    }
    assert any("证据外数字" in item for item in validate_grounded_payload(payload, EVIDENCE))


def test_grounded_claim_accepts_supported_fact():
    payload = {
        "answer_mode": "grounded",
        "claims": [{
            "text": "阿托伐他汀片每片 20mg。",
            "evidence_ids": ["drug:1"],
        }],
    }
    assert validate_grounded_payload(payload, EVIDENCE) == []


def test_answer_evidence_status_distinguishes_grounded_generation():
    status = smart_doctor._evidence_status({
        "interpretation": {
            "source": "AI 智能导诊",
            "items": [{"ref_id": "drug:1"}],
        },
        "advice": {"source": "AI 智能导诊"},
    })
    assert status == {
        "status": "grounded",
        "references": 1,
        "model_used": True,
        "message": "模型仅负责改写，展示内容已通过证据 ID 门禁。",
    }


def test_answer_evidence_status_marks_insufficient_guide():
    status = smart_doctor._evidence_status({
        "interpretation": {
            "source": "项目整理知识库",
            "title": "请换个问法",
            "items": [],
        },
        "advice": {"source": "项目整理知识库"},
    })
    assert status["status"] == "insufficient"
    assert status["model_used"] is False
