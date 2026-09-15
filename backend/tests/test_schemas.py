"""契约模型测试。字段名即前后端契约，不可随意改名。"""
from backend.schemas import QueryRequest, Token


def test_token_types():
    assert Token(type="staff", subject_id=None).type == "staff"
    assert Token(type="patient", subject_id="P001").subject_id == "P001"


def test_query_request_shape():
    req = QueryRequest(
        token=Token(type="patient", subject_id="P001"),
        datasource_id="regional_health",
        question_id="patient_my_lab",
    )
    assert req.datasource_id == "regional_health"
    assert req.question_id == "patient_my_lab"


def test_query_request_serializes_with_expected_keys():
    req = QueryRequest(
        token=Token(type="staff", subject_id=None),
        datasource_id="regional_health",
        question_id="doctor_dept_visits",
    )
    data = req.model_dump()
    assert set(data.keys()) == {"token", "datasource_id", "question_id"}