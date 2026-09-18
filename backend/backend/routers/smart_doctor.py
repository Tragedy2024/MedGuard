"""智慧医生路由（面向患者的可信就医助手，智慧医生.docx）。

调用链：患者提问 → 意图识别 → 医盾管线取本人数据（层一+层二，
与查询控制台同一套审计）→ 知识库解读与建议 → 三块返回。

令牌约束：当前仅面向**病患令牌**（docx 定位是患者助手）；且病患令牌
必须带 subject_id（绑定本人）——没有主语绑定的提问无从"只读本人"。
"""
from fastapi import APIRouter, HTTPException

from backend import smart_doctor
from backend.schemas import SmartDoctorRequest, SmartDoctorResponse
from backend.security import verified

router = APIRouter(prefix="/api/smart-doctor", tags=["smart-doctor"])


@router.post("/ask", response_model=SmartDoctorResponse)
def ask(req: SmartDoctorRequest):
    verified(req.token)
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    if req.token.type != "patient":
        raise HTTPException(
            status_code=403,
            detail="智慧医生当前仅面向病患令牌开放（面向医护的中文问答可到查询控制台）。",
        )
    if not req.token.subject_id:
        raise HTTPException(
            status_code=422,
            detail="病患令牌缺少本人绑定（subject_id），无法保证只读取本人数据。",
        )
    return smart_doctor.ask(question, req.token.subject_id)