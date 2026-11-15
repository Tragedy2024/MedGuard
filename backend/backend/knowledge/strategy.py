"""按任务选择取数方式。规则可追踪，不让模型决定数据访问权限。

借鉴 Adaptive-RAG 的按需选择思想，不复刻其训练分类器或无证据生成分支。
返回的元数据条件由服务器计算，不接受用户指定来源或权限过滤条件。
"""
from dataclasses import asdict, dataclass
import re
from typing import Any, Dict, Optional, Tuple

from backend.knowledge import corpus


DEPARTMENT_QUESTION_SIGNALS = (
    "挂什么科", "看什么科", "去什么科", "到什么科", "挂哪个科", "看哪个科",
    "去哪个科", "到哪个科", "挂哪科", "看哪科", "挂哪一科",
    "看哪一科", "挂啥科", "看啥科", "挂科", "什么科室", "哪个科室",
    "哪方面的大夫", "哪类医生",
)

_FIELD_SIGNALS = {
    "checks": ("什么检查", "哪些检查", "怎么检查", "检查方法"),
    "symptoms": ("什么症状", "哪些症状", "常见症状", "早期表现", "常见表现"),
    "department": (*DEPARTMENT_QUESTION_SIGNALS, "什么科", "哪个科", "哪些科", "科室"),
    "complications": ("并发症",),
    "treatments": ("怎么治疗", "如何治疗", "治疗方法", "治疗方式"),
    "causes": ("病因", "为什么会", "发病原因"),
    "caution": ("副作用", "注意事项", "禁忌", "不良反应"),
    "reference": ("参考范围", "正常范围", "正常值"),
    "purpose": ("干什么用", "有什么用", "起什么作用", "一般是干嘛", "药品用途"),
    "usage": ("用法", "服用方法", "怎么吃", "怎么服用", "用量"),
}
_COMPARE = ("区别", "区分", "不同", "比较", "差别")
_SELF_RECORD = re.compile(
    r"我的|我自己的|我本人|我上(?:次|回)|我最近|我(?:前两天|前几天|昨天|今天|早上)|"
    r"给我开(?:了|的|过)|给我做(?:了|的|过)|医生开的药"
)
_OTHER_PERSON = re.compile(
    r"不是我的|别人的|他人的|另一个人|我(?:的)?(?:爸|妈|父亲|母亲|朋友|同事|孩子|女儿|儿子|爱人)"
)
_RECORD_SIGNALS = {
    "lab": re.compile(r"报告|结果|化验单|数字|数值|正常[吗么]|正不正常|高吗|低吗|多少|查一下|调出来"),
    "medication": re.compile(r"处方|药单|用药记录|给我开|开的药|开了啥|开的啥|开了什么"),
}
VAGUE_SYMPTOM_SIGNALS = ("蔫蔫的", "哪里难受", "浑身不对劲", "感觉不太对劲")


@dataclass(frozen=True)
class RetrievalPlan:
    strategy: str
    reason: str
    filters: corpus.MetadataFilter = corpus.MetadataFilter()

    def describe(self) -> Dict[str, Any]:
        return {"strategy": self.strategy, "reason": self.reason,
                "filters": {k: list(v) for k, v in asdict(self.filters).items() if v}}


def _entities(question: str, kb: Dict[str, Any], disease_names=()) -> Tuple[str, ...]:
    """仅完整名字/规范别名字面命中，去掉被更长实体包含的命中。"""
    hits = []
    for doc in corpus.build_docs(kb):
        for alias in (doc.name, *doc.aliases):
            if alias and alias in question:
                hits.append((alias, doc.entity_id or doc.key, doc.kind))
    for name in disease_names:
        if name and name in question:
            hits.append((name, name, "disease"))
    return tuple(dict.fromkeys(key for alias, key, _ in hits
                              if not any(alias != other and alias in other
                                         for other, _, _ in hits)))


def metadata_for(question: str, kb: Dict[str, Any], *,
                 entity: Optional[str] = None, intent: str = "",
                 disease_names=()) -> corpus.MetadataFilter:
    fields = tuple(k for k, signals in _FIELD_SIGNALS.items()
                   if any(s in question for s in signals))
    entities = _entities(question, kb, disease_names)
    if entity and not entities:
        entities = (entity,)
    # 只有确定知识类型时才限制；不把未识别的口语强行归某个疾病。
    kinds = ()
    if intent == "disease":
        kinds = ("disease",)
    elif intent == "medication":
        kinds = ("medication",)
    elif intent == "lab":
        kinds = ("lab",)
    elif intent == "symptom":
        kinds = ("symptom",)
    return corpus.MetadataFilter(kinds=kinds, entities=entities, fields=fields)


def requested_subtypes(question: str, names) -> Tuple[str, ...]:
    """保留显式子型，也识别“1型和2型糖尿病”的共用尾词。"""
    out = []
    q = corpus.normalize_entity(question)
    if not re.search(r"[12]型", q):
        return ()
    for name in names:
        base = corpus.normalize_entity(name)
        if re.match(r"[12]型", base):
            base = base[2:]
        if base not in q:
            continue
        pattern = r"((?:[12]型\s*(?:和|与|、|及|/)?\s*)+)" + re.escape(base)
        for match in re.finditer(pattern, q):
            out.extend(t + "型" + base for t in re.findall(r"([12])型", match[1]))
    return tuple(dict.fromkeys(out))


def choose(question: str, intent: str, entity: Optional[str], kb: Dict[str, Any], *,
           disease_names=(), personal_followup: bool = False) -> RetrievalPlan:
    filters = metadata_for(question, kb, entity=entity, intent=intent,
                           disease_names=disease_names)
    personal = intent in {"lab", "medication"}
    if personal and not _OTHER_PERSON.search(question):
        explicit_self = _SELF_RECORD.search(question) and _RECORD_SIGNALS[intent].search(question)
        referring_to_record = personal_followup and not {"purpose", "caution"}.intersection(filters.fields)
        if explicit_self or referring_to_record:
            return RetrievalPlan("personal_sql", "读取本人记录，沿用身份准入和 SQL 审计")

    subtypes = requested_subtypes(question, [*kb.get("diseases", {}), *disease_names])
    known = ({corpus.normalize_entity(n) for n in [*kb.get("diseases", {}), *disease_names]}
             if subtypes else set())
    if subtypes and any(name not in known for name in subtypes):
        return RetrievalPlan("insufficient", "缺少所问疾病子型资料，不能用父疾病条目替代",
                             corpus.MetadataFilter(kinds=("disease",), entities=subtypes))
    if subtypes:
        filters = corpus.MetadataFilter(kinds=("disease",), entities=subtypes, fields=filters.fields)

    if any(s in question for s in _COMPARE):
        if len(filters.entities) < 2:
            return RetrievalPlan("clarify", "比较问题需要明确两个对象", filters)
        return RetrievalPlan("hybrid", "比较问题需要检索多个实体的证据", filters)

    if personal:
        if not entity:
            return RetrievalPlan("clarify", "请说明具体检验项或药品名，或明确您要查询本人记录", filters)
        return RetrievalPlan("structured_lookup", "通用检验或药品知识不读取本人记录", filters)
    if intent in {"disease", "symptom"} and entity:
        if any(s in question for s in ("为什么", "原理", "意味着", "解释")):
            return RetrievalPlan("hybrid", "解释问题需要检索与问题匹配的证据", filters)
        return RetrievalPlan("structured_lookup", "已确定实体，优先查询结构化资料", filters)
    if intent == "symptom" and entity is None:
        if any(phrase in question for phrase in VAGUE_SYMPTOM_SIGNALS):
            return RetrievalPlan("clarify", "您主要是觉得没力气、犯困，还是有头晕或其他不适？", filters)
        if filters.fields == ("department",) and any(
                signal in question for signal in DEPARTMENT_QUESTION_SIGNALS):
            return RetrievalPlan("clarify", "请确认具体病名或描述主要症状", filters)
        # 笼统“肚子不舒服”等仍可语义检索；完全没有部位/症状才澄清。
        if question.strip("。？！! ?") in {"我不舒服", "不舒服", "我哪里难受", "我有症状"}:
            return RetrievalPlan("clarify", "尚未提供具体症状或部位", filters)
    return RetrievalPlan("hybrid", "直接查表未能确定答案，按元数据约束混合检索", filters)
