"""数据集知识库：智慧医生的**事实层**，内容全部来自开源数据集 OpenCMKG。

    症状 → 就诊科室      demo/symptom_departments.json
    疾病 → 事实卡片      demo/disease_facts.json

两份文件都由 `scripts/build_dataset_tables.py` 从原始三元组生成——
**抽取逻辑在代码里，可复现可审计**，不是手写一份 JSON 声称来自某数据集。

分工（这是这一层最重要的约定）：

    本模块    只提供**事实**：有哪些症状、归哪个科、该查什么、常用什么药
    AI        提供**解读与建议**，界面上单独标注「AI 智能导诊」

事实与建议**不混同**——这和医盾「零 LLM」的立场是一致的：
能确定的部分不交给模型，交给模型的部分明确标注。

**口语映射不是医学知识，是语言常识**：「嗓子疼」=「咽痛」任何中文使用者
都知道，不涉及"什么病、怎么治"。23 条由数据集的 `SameAs` 同义边自动推导，
8 条人工补（见 build_dataset_tables.py 的 MANUAL_ALIASES）。
"""
import json
import os
import threading
from typing import Any, Dict, List, Optional

from backend import config

_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}

SYMPTOM_FILE = "symptom_departments.json"
DISEASE_FILE = "disease_facts.json"


def _load(filename: str) -> Dict[str, Any]:
    """加载一份数据集表（进程内缓存；读取失败不缓存）。"""
    with _LOCK:
        if filename in _CACHE:
            return _CACHE[filename]
        path = os.path.join(config.DEMO_DIR, filename)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            # 与 smart_doctor.load_knowledge 同样的理由：一次瞬时故障
            # 不该把空表永久留下来
            return {}
        _CACHE[filename] = data
        return data


def source_note(filename: str = SYMPTOM_FILE) -> str:
    """数据出处的展示文案（界面上标来源用）。"""
    meta = _load(filename).get("_meta") or {}
    name = meta.get("source", "")
    if not name:
        return ""
    return f"{name}（开源中文医学知识图谱）"


# ── 症状 → 就诊科室 ──────────────────────────────────────────

def _resolve_symptom(name: str) -> Optional[str]:
    """把患者的说法解析成数据集里的症状名。"""
    if not name:
        return None
    data = _load(SYMPTOM_FILE)
    symptoms = data.get("symptoms") or {}
    if name in symptoms:
        return name

    amap = data.get("alias_map") or {}
    # 人工清单优先——它里面可能有 None，表示"数据集里没有对应的，不硬凑"
    manual = amap.get("manual") or {}
    if name in manual:
        target = manual[name]
        return target if target in symptoms else None

    target = (amap.get("auto") or {}).get(name)
    return target if target in symptoms else None


def lookup_symptom(name: str) -> Optional[Dict[str, Any]]:
    """查一个症状的就诊科室候选。

    Returns: {matched, total, candidates:[{department, disease_count}]} 或 None。
    """
    matched = _resolve_symptom(name)
    if not matched:
        return None
    entry = (_load(SYMPTOM_FILE).get("symptoms") or {}).get(matched)
    if not entry:
        return None
    return {
        "matched": matched,
        "total": entry.get("total", 0),
        "candidates": [
            {"department": d, "disease_count": n}
            for d, n in (entry.get("depts") or [])
        ],
    }


# ── 疾病 → 事实卡片 ──────────────────────────────────────────

# 字段 → 中文标签（界面与 AI 提示词共用）
DISEASE_FIELDS = [
    ("symptoms", "常见症状"),
    ("checks", "常用检查"),
    ("drugs", "常用药物"),
    ("complications", "可能并发症"),
    ("treatments", "治疗方式"),
]


def lookup_disease(name: str) -> Optional[Dict[str, Any]]:
    """查一个疾病的事实卡片。解析不到返回 None。"""
    if not name:
        return None
    diseases = _load(DISEASE_FILE).get("diseases") or {}
    if name in diseases:
        return {"matched": name, "facts": diseases[name]}

    # 疾病名也可能有别名（数据集的 SameAs），目前只做前缀/包含的保守匹配：
    # 「2型糖尿病」→「糖尿病」这类。**不做模糊匹配**——错配疾病比答不上来更糟。
    for d in diseases:
        if d and (d in name or name in d):
            return {"matched": d, "facts": diseases[d]}
    return None


def disease_fact_lines(facts: Dict[str, Any]) -> List[Dict[str, str]]:
    """把事实卡片转成界面用的条目（复用 answer 的 items 结构）。"""
    out: List[Dict[str, str]] = []
    if facts.get("dept"):
        out.append({"ref_title": facts["dept"], "ref_kind": "就诊科室",
                    "ref_source": source_note(DISEASE_FILE),
                    "ref_excerpt": "数据集给出的科室归属"})
    for key, label in DISEASE_FIELDS:
        vals = facts.get(key) or []
        if vals:
            out.append({"ref_title": label, "ref_kind": "数据集事实",
                        "ref_source": source_note(DISEASE_FILE),
                        "ref_excerpt": "、".join(vals)})
    return out
