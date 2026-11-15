"""智慧医生生成内容的确定性证据门禁。

模型只负责把证据改写成人能读懂的话；本模块决定这段话是否有资格展示。
结构校验、证据 ID、数字和药品名均由普通代码检查，不把“模型自证清白”
当作安全机制。校验失败时，调用方必须回到确定性证据模板。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

_NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
_MEDICINE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:片|胶囊|颗粒|注射液|口服液|滴丸|软膏|喷雾剂)"
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate_grounded_payload(
    payload: Dict[str, Any], evidence: Sequence[Dict[str, Any]],
) -> List[str]:
    """返回全部校验错误；空列表表示可展示。

    ``medical_fact`` 的每条主张必须引用本次实际检索到的证据。主张里的
    数字和带常见剂型后缀的药品名必须逐字存在于所引证据，避免剂量漂移和
    “厄贝沙坦片→厄沙坦片”这类近形改写。
    """
    errors: List[str] = []
    valid = {
        str(item.get("ref_id")): item
        for item in evidence if item.get("ref_id")
    }
    mode = payload.get("answer_mode")
    claims = payload.get("claims")
    if mode not in {"grounded", "insufficient"}:
        errors.append("answer_mode 必须是 grounded 或 insufficient")
    if not isinstance(claims, list):
        return [*errors, "claims 必须是数组"]
    if mode == "insufficient":
        if claims:
            errors.append("证据不足时不得输出医学事实")
        return errors
    if not valid:
        errors.append("没有可引用的证据")
    if not claims:
        errors.append("grounded 回答至少需要一条主张")

    for index, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            errors.append(f"第{index}条主张不是对象")
            continue
        text = _text(claim.get("text"))
        ids = claim.get("evidence_ids")
        if not text:
            errors.append(f"第{index}条主张为空")
        if not isinstance(ids, list) or not ids:
            errors.append(f"第{index}条主张没有证据ID")
            continue
        unknown = [str(x) for x in ids if str(x) not in valid]
        if unknown:
            errors.append(f"第{index}条主张引用不存在的证据：{','.join(unknown)}")
            continue
        cited = " ".join(
            f"{valid[str(eid)].get('ref_title', '')} "
            f"{valid[str(eid)].get('ref_excerpt', '')}"
            for eid in ids
        )
        for number in _NUMBER.findall(text):
            if number not in cited:
                errors.append(f"第{index}条主张含证据外数字：{number}")
        for medicine in _MEDICINE.findall(text):
            if medicine not in cited:
                errors.append(f"第{index}条主张含证据外药品名：{medicine}")
    return errors


def render_grounded_text(payload: Dict[str, Any]) -> str:
    """只渲染已经通过 ``validate_grounded_payload`` 的结构。"""
    claims = payload.get("claims") or []
    parts = [_text(item.get("text")) for item in claims if isinstance(item, dict)]
    uncertainty = _text(payload.get("uncertainty"))
    if uncertainty:
        parts.append(uncertainty)
    return " ".join(part for part in parts if part)
