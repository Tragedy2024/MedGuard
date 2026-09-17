"""智慧医生——面向患者的"可信就医助手"（智慧医生.docx）。

职责：理解患者意图 → 经医盾管线（层一准入 + 层二审计）只读**本人**数据
→ 用医院审核知识库（demo/smart_knowledge.yaml）产出三块内容：
  1. 院内数据    （来源：医院主库）——查询计划照常走层一/层二
  2. 通俗解读    （来源：医院审核知识库）
  3. 下一步建议  （来源：医院审核知识库）

知识库未覆盖的问法（其他不舒服的症状、未收录的疾病等）→ **AI 兜底**：
调用与 NL→SQL 同一通道的大模型生成三块回答，来源标注「AI 智能导诊」，
与人工审核的知识库诚实区分。未配置 API Key 或调用失败时自动回退到
确定性引导文案，**绝不 500**。

关键设计：
- **医盾管线完全复用**：取数走的计划与查询控制台一样，先 `admission_check_plan`
  再 `deps.audit_plan`，层一确保"只能读取本人"，层二确保中间结果不泄露。
  自由提问（/api/query/direct）缓存里的计划同样适用——本模块构造的计划
  目标是验证过的物理名，直接构造即可。
- **知识库优先，AI 兜底**：知识库是人工审核的确定性内容（离线、毫秒级）；
  只有知识库没覆盖的问法才交给 AI。涉及本人数据的意图（lab / medication）
  永远不把数据发给模型——AI 只做解释，不碰数据。
"""
import os
import re
import sqlite3
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

import yaml

from backend import config
from backend.admission import admission_check_plan
from backend.deps import audit_plan

# ── 意图类型 ──────────────────────────────────────────────────
INTENT_LAB = "lab"            # 帮我看懂检查报告 / 我的血糖结果正常吗
INTENT_MEDICATION = "medication"  # 医生开的药怎么吃
INTENT_SYMPTOM = "symptom"    # 我不舒服，不知道挂什么科
INTENT_DISEASE = "disease"    # 我想了解一种疾病
INTENT_FALLBACK = "fallback"  # 没能识别

_SOURCE_DATA = "医院主库"
_SOURCE_KB = "医院审核知识库"
_SOURCE_AI = "AI 智能导诊"

_LOCK = threading.Lock()
_KB: Optional[Dict[str, Any]] = None


# ── 知识库加载 ────────────────────────────────────────────────

def load_knowledge() -> Dict[str, Any]:
    """加载医院审核知识库（进程内缓存，文件损坏时返回空结构）。"""
    global _KB
    if _KB is not None:
        return _KB
    path = os.path.join(config.DEMO_DIR, "smart_knowledge.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    with _LOCK:
        _KB = data
    return data


# ── 意图识别（确定性关键词，零 LLM） ──────────────────────────

_ASK_DEPT = ("挂什么科", "看什么科", "挂哪个科", "该挂", "挂科", "看哪个科",
             "去哪个科", "什么科室")
_MED_SIGNALS = ("怎么吃", "怎么服用", "用法", "用量", "吃法", "服药", "吃药",
                "服用")
_LAB_SIGNALS = ("报告", "结果", "检查", "化验", "指标", "正常吗", "正常么",
                "参考范围", "检测")
_DISEASE_SIGNALS = ("想了解", "是什么病", "这种病", "这个病", "了解一下",
                    "科普")


def _find_entity(question: str, table: Dict[str, Any]) -> Optional[str]:
    """在知识库某表里找命中的键（如 lab_tests 里的「血糖」）。"""
    for key in table:
        if key and key in question:
            return key
    return None


def parse_intent(question: str) -> Tuple[str, Optional[str]]:
    """返回 (intent, entity)。entity 为命中的具体检验项/药名/症状关键词/疾病名。

    优先级（写成显式顺序，避免歧义）：
      挂科意图 > 用药意图 > 报告解读意图 > 疾病了解意图 > 症状 ? > fallback
    """
    kb = load_knowledge()
    lab_tests = kb.get("lab_tests") or {}
    medications = kb.get("medications") or {}
    diseases = kb.get("diseases") or {}
    symptoms = kb.get("symptoms") or []

    # 0) 明确要挂科 → symptom（感冒了该挂什么科，不能落到 disease）
    if any(s in question for s in _ASK_DEPT):
        for sym in symptoms:
            for k in (sym.get("keys") or []):
                if k and k in question:
                    return INTENT_SYMPTOM, k
        return INTENT_SYMPTOM, None

    # 1) 用药（命中药名或出现"怎么吃/用法"信号）
    drug = _find_entity(question, medications)
    if drug:
        return INTENT_MEDICATION, drug
    if any(s in question for s in _MED_SIGNALS):
        return INTENT_MEDICATION, None

    # 2) 检查报告（命中检验项名，或出现"报告/结果/正常吗"等信号）
    test = _find_entity(question, lab_tests)
    if test:
        return INTENT_LAB, test
    if any(s in question for s in _LAB_SIGNALS):
        return INTENT_LAB, None

    # 3) 疾病了解
    for dname, d in diseases.items():
        for k in (d.get("keywords") or []):
            if k and k in question:
                return INTENT_DISEASE, (k if k != dname else dname)
    if any(s in question for s in _DISEASE_SIGNALS):
        return INTENT_DISEASE, None

    # 4) 症状
    for sym in symptoms:
        for k in (sym.get("keys") or []):
            if k and k in question:
                return INTENT_SYMPTOM, k
    if any(s in question for s in ("不舒服", "症状", "哪里难受")):
        return INTENT_SYMPTOM, None

    return INTENT_FALLBACK, None


# ── 医盾管线取数（只读本人） ──────────────────────────────────

def _build_lab_plan(subject: str, test_name: Optional[str], limit: int = 3) -> List[dict]:
    where = (f"AND c.test_name = '{test_name}'" if test_name else "")
    return [{
        "id": 0,
        "description": f"读取本人最近检验结果{('（' + test_name + '）') if test_name else ''}",
        "sql": (
            "SELECT c.test_name, c.result_value, c.unit, v.visit_date "
            "FROM clinical_records c JOIN visits v ON c.visit_id = v.visit_id "
            f"WHERE c.patient_id = '{subject}' AND c.record_type = 'lab' "
            f"{where} "
            "ORDER BY v.visit_date DESC, c.record_id DESC "
            f"LIMIT {limit}"
        ),
    }]


def _build_medication_plan(subject: str, drug: Optional[str], limit: int = 5) -> List[dict]:
    where = (f"AND c.drug_name = '{drug}'" if drug else "")
    return [{
        "id": 0,
        "description": f"读取本人用药记录{('（' + drug + '）') if drug else ''}",
        "sql": (
            "SELECT c.drug_name, c.dosage, c.frequency, c.route, v.visit_date "
            "FROM clinical_records c JOIN visits v ON c.visit_id = v.visit_id "
            f"WHERE c.patient_id = '{subject}' AND c.record_type = 'medication' "
            f"{where} "
            "ORDER BY v.visit_date DESC, c.record_id DESC "
            f"LIMIT {limit}"
        ),
    }]


def _execute(sql: str) -> Tuple[Optional[List[str]], List[List[Any]]]:
    """执行单条 SQL（与查询路由同一套连接配置）。失败返回 (None, [])。"""
    db = config.BUSINESS_DB
    if not db or not os.path.exists(db):
        return None, []
    con = sqlite3.connect(db)
    con.text_factory = lambda b: b.decode(errors="ignore")
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        return cols, rows
    except sqlite3.Error:
        return None, []
    finally:
        con.close()


def fetch_personal_data(plan: List[dict], subject: str
                        ) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[List[str]], List[List[Any]], str]:
    """走层一 + 层二拿到本人数据。

    Returns:
        (admission_dict, degradation_dict, columns, rows, sql_after)
    - admission_dict / degradation_dict：与查询路由同构，前端可直接复用
    - 层一拒绝时 columns=None（不发查询、不执行）
    - sql_after：层二改写后的最终 SQL（展示"医盾拦了什么"的证据）
    """
    adm = admission_check_plan(plan, subject)
    if not adm.passed:
        return adm.to_dict(), {"level": "L3", "label": "已拒绝",
                               "message_cn": adm.reason or "", "message": ""}, \
            None, [], plan[0]["sql"]

    outcome = audit_plan(plan, config.DEMO_DATASOURCE_ID)
    sql_after = {sq["id"]: sq["sql"] for sq in outcome.audited_plan}
    if not sql_after:
        sql_after = {sq["id"]: sq["sql"] for sq in plan}

    if outcome.degradation_level == "L3":
        return adm.to_dict(), {"level": "L3", "label": "已拒绝",
                               "message_cn": outcome.degradation_message,
                               "message": outcome.degradation_message}, \
            None, [], sql_after.get(plan[-1]["id"], "")

    final_id = plan[-1]["id"]
    final_sql = sql_after.get(final_id, "")
    cols, rows = _execute(final_sql)

    # 层二改写后的 SQL 一并返回给前端展示（"医盾拦截了什么"的证据）
    return adm.to_dict(), {"level": outcome.degradation_level,
                           "label": outcome.degradation_label or "",
                           "message_cn": outcome.degradation_message_cn or "",
                           "message": outcome.degradation_message}, \
        cols, rows, final_sql


# ── 解读与建议（知识库） ──────────────────────────────────────

def _parse_number(text: str) -> Optional[float]:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def interpret_lab(test_name: str, value_text: str) -> Optional[Dict[str, Any]]:
    """按数值分档解读一个检验结果。返回 {label, text} 或 None（知识缺失）。"""
    kb = load_knowledge()
    entry = (kb.get("lab_tests") or {}).get(test_name)
    if not entry:
        return None
    value = _parse_number(value_text or "")
    if value is None:
        return {"label": "已出结果", "text": "该结果已出，具体解读请结合临床由医生判读。"}
    # 档位从上到下，第一个「下界与上界同时满足」的生效
    for r in entry.get("ranges") or []:
        lo = r.get("min")
        hi = r.get("max")
        if (lo is None or value >= lo) and (hi is None or value <= hi):
            return {"label": r.get("label", ""), "text": r.get("text", "")}
    return {"label": "已出结果", "text": "该结果已出，具体解读请结合临床由医生判读。"}


# ── AI 兜底（知识库未覆盖的问法） ─────────────────────────────
#
# 与 NL→SQL 翻译同一通道（vendor 内置 MAC-SQL 体系的 core.llm）。
# 只用于**咨询类**问法（症状/疾病/未识别）；lab 与 medication 涉及
# 本人数据读取，永远不把数据发给模型——AI 只做解释，不碰数据。
# 未配置 Key / 调用失败 / 输出无法解析 → 返回 None，由调用方回退引导。

_AI_SYSTEM = """你是医院审核知识库的临床导诊专家。请用通俗易懂的中文回答患者的健康咨询。

要求：
1. 只回答医疗知识层面的建议，不得编造患者本人的检查数据、诊断结果或用药记录。
2. 明确说明是否需要就医、建议咨询哪个科室；出现危急信号（剧烈胸痛、呼吸困难、意识模糊、大出血等）必须给出紧急就医提示。
3. 措辞谨慎：不替患者下诊断结论，建议以医生面诊为准。

只输出一个 JSON，不要输出任何其他文字：
{"title": "对咨询内容的简短标题（10字内）", "text": "通俗解释（2-4句）", "advice": "建议（是否需要就医、挂什么科、做什么）", "actions": ["行动建议1", "行动建议2", "行动建议3"], "urgent": true 或 false}"""


def _call_llm(prompt: str) -> str:
    """调用内置算法层的大模型通道（与 llm_nl2sql 同一体系）。"""
    src = config.ALGO_SRC_DIR
    if not src or not os.path.isdir(src):
        raise RuntimeError("算法层（vendor/nl2sql/src）不可用")
    if src not in sys.path:
        sys.path.insert(0, src)
    from core.llm import safe_call_llm  # 延迟导入：无 Key 时也可加载本模块
    return safe_call_llm(prompt)


def _parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出里提取 JSON 对象（容忍围栏、前后废话、脏文本）。"""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    import json
                    data = json.loads(text[start:i + 1])
                    return data if isinstance(data, dict) else None
                except (ValueError, TypeError):
                    return None
    return None


def ai_triage(question: str) -> Optional[Dict[str, Any]]:
    """AI 兜底作答。成功返回三块内容，失败返回 None（调用方回退引导）。"""
    if not config.LLM_API_KEY:
        return None
    prompt = (
        f"{_AI_SYSTEM}\n\n"
        f"患者的咨询内容：{question.strip()}\n\n"
        "请按要求输出 JSON。"
    )
    try:
        raw = _call_llm(prompt)
    except Exception as exc:  # noqa: BLE001 - 网络/鉴权异常统一回退
        print(f"[smart-doctor] AI 兜底调用失败，回退引导：{exc}", flush=True)
        return None

    data = _parse_llm_json(raw)
    if not data:
        # 输出无法解析：只保留一段文本，不返回空壳结构
        snippet = " ".join((raw or "").split())
        if not snippet:
            return None
        return {
            "title": "AI 智能导诊",
            "text": snippet[:500],
            "advice": "如症状持续或加重，请及时就医面诊。",
            "actions": ["症状加重及时就医"],
            "urgent": False,
        }

    def _s(*keys: str, default: str = "") -> str:
        for k in keys:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return default

    actions = [str(a).strip() for a in (data.get("actions") or [])
               if isinstance(a, str) and str(a).strip()]
    advice = _s("advice", default="如症状持续或加重，请及时就医面诊。")
    return {
        "title": _s("title", default="AI 智能导诊"),
        "text": _s("text", default=advice),
        "advice": advice,
        "actions": actions or ["症状持续或加重请及时就医"],
        "urgent": bool(data.get("urgent")),
    }


def _apply_ai_fallback(base: Dict[str, Any], question: str) -> bool:
    """填 AI 兜底回答（来源：AI 智能导诊）。成功返回 True，失败返回 False。"""
    ai = ai_triage(question)
    if ai is None:
        return False
    base["interpretation"] = {
        "source": _SOURCE_AI,
        "title": ai["title"],
        "text": ai["text"],
        "items": [],
    }
    base["advice"] = {
        "source": _SOURCE_AI,
        "text": ai["advice"],
        "actions": ai["actions"],
        "urgent": ai["urgent"],
    }
    return True


# ── 入口 ──────────────────────────────────────────────────────

def ask(question: str, subject_id: str) -> Dict[str, Any]:
    """处理一次提问，返回三块结构的响应（供路由直接返回）。"""
    kb = load_knowledge()
    intent, entity = parse_intent(question)
    subject = subject_id or ""

    base = {
        "intent": intent,
        "question": question,
        "data": None,
        "interpretation": None,
        "advice": None,
    }

    # ── 检查报告解读 ──
    if intent == INTENT_LAB:
        plan = _build_lab_plan(subject, entity)
        adm, deg, cols, rows, final_sql = fetch_personal_data(plan, subject)
        base["admission"] = adm
        base["degradation"] = deg
        if cols is None:
            # 层一拒绝（理论少见：计划自带本人绑定；保留路径以防计划被改）
            base["interpretation"] = {
                "source": _SOURCE_KB,
                "title": "无法读取您的检查数据",
                "text": adm.get("reason") or "该查询涉及其他患者信息，无法提供。",
                "items": [],
            }
            base["advice"] = {
                "source": _SOURCE_KB, "text": "如有疑问请联系医院信息科。",
                "actions": [], "urgent": False,
            }
            return base

        base["data"] = {
            "source": _SOURCE_DATA,
            "columns": cols or [],
            "rows": rows,
            "sql_after": final_sql,
            "degradation_level": deg.get("level", "L0"),
        }

        items = []
        if entity is None and rows:
            entity = str(rows[0][0])  # 未指定检验项 → 以最近一条为准
        for row in rows:
            name, val, unit, date = (list(row) + [None, None, None, None])[:4]
            hit = interpret_lab(str(name), str(val or ""))
            items.append({
                "test_name": str(name), "result_value": str(val or ""),
                "unit": str(unit or ""), "visit_date": str(date or ""),
                "label": (hit or {}).get("label", ""),
                "text": (hit or {}).get("text", ""),
            })
        entry = (kb.get("lab_tests") or {}).get(str(entity))
        base["interpretation"] = {
            "source": _SOURCE_KB,
            "title": f"{entity}结果解读" if items else "未找到检验记录",
            "text": (entry or {}).get("desc", ""),
            "items": items,
        }
        advice_text = (entry or {}).get("advice_common") or []
        actions = list(advice_text) if isinstance(advice_text, list) else []
        urgent = bool((entry or {}).get("urgent_text"))
        text = "；".join(actions) if actions else ""
        if urgent:
            text = (text + "。" if text else "") + (entry or {}).get("urgent_text", "")
        base["advice"] = {
            "source": _SOURCE_KB,
            "text": text or "建议将本次结果带给您的主治医生做整体评估。",
            "actions": actions,
            "urgent": urgent,
        }
        if not items:
            base["advice"]["text"] = "未在您的记录中找到相关检验，可到查询控制台确认或在下次化验后查看。"
        return base

    # ── 用药说明 ──
    if intent == INTENT_MEDICATION:
        plan = _build_medication_plan(subject, entity)
        adm, deg, cols, rows, final_sql = fetch_personal_data(plan, subject)
        base["admission"] = adm
        base["degradation"] = deg
        if cols is None:
            base["interpretation"] = {
                "source": _SOURCE_KB, "title": "无法读取您的用药记录",
                "text": adm.get("reason") or "该查询涉及其他患者信息，无法提供。",
                "items": [],
            }
            base["advice"] = {"source": _SOURCE_KB, "text": "如有疑问请联系医院信息科。",
                              "actions": [], "urgent": False}
            return base

        base["data"] = {
            "source": _SOURCE_DATA,
            "columns": cols or [],
            "rows": rows,
            "sql_after": final_sql,
            "degradation_level": deg.get("level", "L0"),
        }
        items = []
        for row in rows:
            drug = str(row[0] or "")
            info = (kb.get("medications") or {}).get(drug)
            items.append({
                "drug_name": drug,
                "dosage": str(row[1] or ""), "frequency": str(row[2] or ""),
                "route": str(row[3] or ""), "visit_date": str(row[4] or ""),
                "purpose": (info or {}).get("purpose", ""),
                "usage": (info or {}).get("usage", ""),
                "caution": (info or {}).get("caution", ""),
                "note": (info or {}).get("note", ""),
            })
        base["interpretation"] = {
            "source": _SOURCE_KB,
            "title": "用药说明",
            "text": "以下说明来自医院审核知识库，具体请以医嘱与药品说明书为准。",
            "items": items,
        }
        base["advice"] = {
            "source": _SOURCE_KB,
            "text": "遵医嘱按剂量与频次服用，出现不适或疑问及时咨询医生或药师。",
            "actions": ["按医嘱足量足疗程", "出现不适及时联系医生"],
            "urgent": False,
        }
        if not items:
            base["interpretation"]["title"] = "未找到用药记录"
            base["interpretation"]["items"] = []
        return base

    # ── 症状 → 科室 ──
    if intent == INTENT_SYMPTOM:
        base["admission"] = {"passed": True, "reason": None,
                             "checked_tables": [], "bound_to_subject": False}
        base["degradation"] = {"level": "L0", "label": "通过",
                               "message_cn": "", "message": ""}
        hit = None
        if entity:
            for sym in kb.get("symptoms") or []:
                if entity in (sym.get("keys") or []):
                    hit = sym
                    break
        if hit:
            base["interpretation"] = {
                "source": _SOURCE_KB,
                "title": f"关于「{entity}」",
                "text": hit.get("text", ""),
                "items": [],
            }
            base["advice"] = {
                "source": _SOURCE_KB,
                "text": f"建议优先咨询{hit.get('department', '相应科室')}。"
                        f"（紧急程度参考：{hit.get('severe', '轻度')}）",
                "actions": [f"请咨询{hit.get('department', '相应科室')}",
                            "症状加重或持续不缓解请及时就医"],
                "urgent": hit.get("severe") in ("高度", "中度"),
            }
        else:
            # 知识库未覆盖的症状（如「我膝盖疼」「我拉肚子」）→ AI 兜底
            if _apply_ai_fallback(base, question):
                return base
            base["interpretation"] = {
                "source": _SOURCE_KB,
                "title": "请描述您的具体症状",
                "text": "您可以说「我胸痛」「我头晕」这类具体症状，我会帮您判断该咨询哪个科室。",
                "items": [],
            }
            base["advice"] = {
                "source": _SOURCE_KB, "text": "急性剧烈不适请直接急诊就诊。",
                "actions": ["描述具体症状重试"], "urgent": False,
            }
        return base

    # ── 疾病了解 ──
    if intent == INTENT_DISEASE:
        base["admission"] = {"passed": True, "reason": None,
                             "checked_tables": [], "bound_to_subject": False}
        base["degradation"] = {"level": "L0", "label": "通过",
                               "message_cn": "", "message": ""}
        hit = None
        diseases = kb.get("diseases") or {}
        if entity:
            for dname, d in diseases.items():
                if entity in (d.get("keywords") or []) or entity == dname:
                    hit = d
                    entity = dname
                    break
        if hit:
            base["interpretation"] = {
                "source": _SOURCE_KB,
                "title": f"关于「{entity}」",
                "text": hit.get("what", ""),
                "items": [],
            }
            base["advice"] = {
                "source": _SOURCE_KB,
                "text": hit.get("advice", ""),
                "actions": [s.strip() for s in
                            re.split(r"[；;]", hit.get("advice", "")) if s.strip()],
                "urgent": False,
            }
        else:
            # 知识库未收录的疾病 → AI 兜底
            if _apply_ai_fallback(base, question):
                return base
            base["interpretation"] = {
                "source": _SOURCE_KB,
                "title": "暂未收录该疾病",
                "text": "您可以直接问「我想了解糖尿病」这类常见疾病，或到查询控制台查阅您的检查结果。",
                "items": [],
            }
            base["advice"] = {"source": _SOURCE_KB, "text": "",
                              "actions": [], "urgent": False}
        return base

    # ── 兜底（未识别的问法）──
    base["admission"] = {"passed": True, "reason": None,
                         "checked_tables": [], "bound_to_subject": False}
    base["degradation"] = {"level": "L0", "label": "通过",
                           "message_cn": "", "message": ""}
    if _apply_ai_fallback(base, question):
        return base
    base["interpretation"] = {
        "source": _SOURCE_KB,
        "title": "换个问法试试",
        "text": "可以这样问我：\n"
                "· 我不舒服，不知道挂什么科（再说说具体症状）\n"
                "· 帮我看懂检查报告 / 我的血糖结果正常吗\n"
                "· 医生开的药怎么吃\n"
                "· 我想了解糖尿病",
        "items": [],
    }
    base["advice"] = {"source": _SOURCE_KB, "text": "",
                      "actions": [], "urgent": False}
    return base