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
    "checks": ("什么检查", "哪些检查", "怎么检查", "检查方法",
               "有哪几种检查", "有哪些检查", "有什么检查"),
    "symptoms": ("什么症状", "哪些症状", "常见症状", "早期表现", "常见表现",
                 "有哪几种症状", "有哪些症状", "有什么症状"),
    "department": (*DEPARTMENT_QUESTION_SIGNALS, "什么科", "哪个科", "哪些科", "科室"),
    "complications": ("并发症",),
    "treatments": ("怎么治疗", "如何治疗", "治疗方法", "治疗方式",
                   "能治好吗", "可以治好吗", "治愈"),
    "causes": ("病因", "为什么会", "发病原因", "什么原因", "原因引起",
               "怎么引起", "遗传吗", "遗传", "传染吗", "传染"),
    "drugs": ("用什么药", "吃什么药", "什么药", "哪些药",
              "有哪几种药", "药物", "用药"),
    "severity": ("严重吗", "严重么", "严重", "危险吗", "风险"),
    "caution": ("副作用", "注意事项", "禁忌", "不良反应"),
    "reference": ("参考范围", "正常范围", "正常值"),
    "purpose": ("干什么用", "有什么用", "起什么作用", "一般是干嘛", "药品用途"),
    "usage": ("用法", "服用方法", "怎么吃", "怎么服用", "用量"),
}
_COMPARE = ("区别", "区分", "不同", "比较", "差别",
            "哪个更严重", "哪个严重", "哪个更危险", "哪个更厉害",
            "更严重", "更危险")
# “糖尿病有哪几种/分几种/有哪些类型”问的是疾病分型，不是普通事实字段。
# 但“有哪几种并发症/检查/药/治疗方法”仍是字段问题；用负向前瞻挡住。
_SUBTYPE_ENUM = re.compile(
    r"(?:(?:有|分)(?:哪|几)(?:几)?(?:种|型|类)|有几种(?:类型|分型)?)"
    r"(?!\s*(?:并发症|症状|检查|检验|治疗|药物|药|原因|表现|科室|医院|医生|费用|风险|危害|人群|人))"
    r"|有哪些类型|哪种类型|什么类型|分型"
)
_ROMAN_TYPE = re.compile(r"^(?:[12]|一|二|Ⅰ|Ⅱ|I|II)型")
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


_COMPOUND_SEP = re.compile(r"[和与、及/]")


def _split_compound(name: str,
                    canonical_of: Dict[str, str]) -> Tuple[str, ...]:
    """把「糖尿病和高血压」这类合并病名拆成两个已知实体。

    OpenCMKG 是从网页聚合的图谱，疾病名里混着「A和B」这种合并条目。
    如果按最长字面匹配把它当成一个实体，A 和 B 都会被它“包住”而丢掉，
    比较题于是被误判成「需要明确两个对象」。

    只在**所有部分都能映射到规范实体键**时才拆；返回的是规范键，
    不是用户原话——「心梗」要映射到「心肌梗死」，否则元数据过滤
    匹配不到知识库条目。否则保留原名：宁可把合并名当一个条目，
    也不要把一个真实病名拆错。
    """
    if not _COMPOUND_SEP.search(name):
        return ()
    parts = tuple(part.strip() for part in _COMPOUND_SEP.split(name) if part.strip())
    if len(parts) < 2:
        return ()
    keys = []
    for part in parts:
        key = canonical_of.get(corpus.normalize_entity(part))
        if not key:
            return ()
        keys.append(key)
    if len(set(keys)) < 2:
        return ()
    return tuple(dict.fromkeys(keys))


def _known_subtypes(entity: str, names) -> Tuple[str, ...]:
    """列出数据集/知识库中能确认属于该疾病的分型条目。

    只认“1型/2型/一型/二型/Ⅰ型/Ⅱ型/I型/II型”这类前缀 + 父疾病名，
    避免把“糖尿病肾病”“小儿糖尿病”误当成分型。
    """
    parent = corpus.normalize_entity(entity)
    out = []
    for name in names:
        if not name or name == entity:
            continue
        normalized = corpus.normalize_entity(name)
        if parent and parent in normalized and (
                _ROMAN_TYPE.match(name.strip()) or _ROMAN_TYPE.match(normalized)):
            out.append(name)
    return tuple(dict.fromkeys(out))


def _entities(question: str, kb: Dict[str, Any], disease_names=(), *,
              kinds: Tuple[str, ...] = ()) -> Tuple[str, ...]:
    """仅完整名字/规范别名字面命中，去掉被更长实体包含的命中。

    ``kinds`` 限定只在某一类文档里找实体：检验问题里疾病别名「血糖高」
    不能把检验项「血糖」挤掉；检验/药品/症状问题也不应借用疾病实体。
    """
    docs = corpus.build_docs(kb)
    if kinds:
        docs = [doc for doc in docs if doc.kind in kinds]
    use_disease_names = not kinds or "disease" in kinds
    canonical_of: Dict[str, str] = {}
    for doc in docs:
        key = doc.entity_id or doc.key
        for alias in (doc.name, *doc.aliases):
            if alias:
                canonical_of.setdefault(corpus.normalize_entity(alias), key)
    if use_disease_names:
        for name in disease_names:
            if name:
                canonical_of.setdefault(corpus.normalize_entity(name), name)

    hits = []
    for doc in docs:
        for alias in (doc.name, *doc.aliases):
            if alias and alias in question:
                hits.append((alias, doc.entity_id or doc.key, doc.kind))
    if use_disease_names:
        for name in disease_names:
            if name and name in question:
                parts = _split_compound(name, canonical_of)
                if parts:
                    hits.extend((part, part, "disease") for part in parts)
                else:
                    hits.append((name, name, "disease"))
    return tuple(dict.fromkeys(key for alias, key, _ in hits
                              if not any(alias != other and alias in other
                                         for other, _, _ in hits)))


def metadata_for(question: str, kb: Dict[str, Any], *,
                 entity: Optional[str] = None, intent: str = "",
                 disease_names=()) -> corpus.MetadataFilter:
    fields = tuple(k for k, signals in _FIELD_SIGNALS.items()
                   if any(s in question for s in signals))
    entity_kinds = {
        "disease": ("disease",),
        "medication": ("medication",),
        "lab": ("lab",),
        "symptom": ("symptom",),
    }.get(intent, ())
    entities = _entities(question, kb, disease_names, kinds=entity_kinds)
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

    # 分型枚举问题：不能拿父疾病的事实卡冒充完整分型列表。
    # 只有确认至少两个子型条目时才进入检索；否则如实说资料不足。
    if _SUBTYPE_ENUM.search(question) and entity:
        known = _known_subtypes(
            entity, [*kb.get("diseases", {}), *disease_names])
        if len(known) < 2:
            return RetrievalPlan(
                "insufficient",
                "当前资料未收录该疾病的完整分型条目，不能用父疾病条目替代",
                corpus.MetadataFilter(kinds=("disease",), entities=(entity,)),
            )
        return RetrievalPlan(
            "subtype_lookup",
            "按已收录子型条目列出分型",
            corpus.MetadataFilter(kinds=("disease",), entities=known),
        )
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
        if subtypes and len(subtypes) >= 2:
            return RetrievalPlan("subtype_lookup",
                                 "比较分型需要列出各子型证据", filters)
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
