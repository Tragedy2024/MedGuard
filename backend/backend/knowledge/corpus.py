"""知识库语料层：把 smart_knowledge.yaml 展平成统一的「文档」。

为什么需要这一层：语料按人类可读的方式分段（lab_tests / medications /
symptoms / diseases / departments），而检索关心的是「有哪些可被检索的条目」。
让检索代码去理解五种段落形状，会把分段细节漏得到处都是；在这里一次性
展平，检索层只需要面对一种东西。

**这一层不做任何医学判断**，只做形状转换——正文怎么拼、别名从哪取。
"""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

# 文档类型。与语料的段落名一致（除 departments）。
KIND_LAB = "lab"
KIND_MEDICATION = "medication"
KIND_SYMPTOM = "symptom"
KIND_DISEASE = "disease"
KIND_DEPARTMENT = "department"

KIND_LABELS = {
    KIND_LAB: "检验项",
    KIND_MEDICATION: "药品",
    KIND_SYMPTOM: "症状",
    KIND_DISEASE: "疾病",
    KIND_DEPARTMENT: "科室",
}


@dataclass(frozen=True)
class Doc:
    """一条可检索的知识条目。

    key   —— 在原始语料里的定位键。lab_tests/medications/diseases 是 YAML 的键
             （`血糖`），symptoms 是条目的 id（`symptom_chest`）——因为症状
             本来就没有单一名字，它们是一簇近义说法。
    name  —— 展示名。取第一个别名——对症状而言就是「胸痛」这样的代表词。
    text  —— 条目正文。**用于按相关性检索**（见 doc_text_for_index 的说明）。
    """
    id: str
    kind: str
    key: str
    name: str
    aliases: Tuple[str, ...]
    text: str
    source: str = ""


def _join(*parts: Any) -> str:
    """把若干可能是 None / list / str 的字段拼成一段正文。"""
    out: List[str] = []
    for p in parts:
        if p is None:
            continue
        if isinstance(p, str):
            if p.strip():
                out.append(p.strip())
        elif isinstance(p, (list, tuple)):
            out.extend(str(x).strip() for x in p if str(x).strip())
        elif isinstance(p, dict):
            out.append(" ".join(str(v) for v in p.values() if v))
        else:
            out.append(str(p))
    return " ".join(out)


def _source(entry: Dict[str, Any]) -> str:
    """条目的出处（`provenance.source`）。界面要显示"参考了哪一条、谁审的"。"""
    p = entry.get("provenance")
    return p.get("source", "") if isinstance(p, dict) else ""


def _aliases(entry: Dict[str, Any]) -> Tuple[str, ...]:
    a = entry.get("aliases")
    if not isinstance(a, list):
        return ()
    return tuple(x.strip() for x in a if isinstance(x, str) and x.strip())


def _lab_doc(key: str, e: Dict[str, Any]) -> Doc:
    ranges = e.get("ranges") or []
    band_text = _join([r.get("label") for r in ranges],
                      [r.get("text") for r in ranges])
    return Doc(
        id=e.get("id") or f"lab_{key}",
        kind=KIND_LAB, key=key, name=key,
        aliases=_aliases(e),
        source=_source(e),
        text=_join(e.get("desc"), e.get("unit"), e.get("normal"),
                   band_text, e.get("advice_common"), e.get("urgent_text")),
    )


def _medication_doc(key: str, e: Dict[str, Any]) -> Doc:
    return Doc(
        id=e.get("id") or f"drug_{key}",
        kind=KIND_MEDICATION, key=key, name=key,
        aliases=_aliases(e),
        source=_source(e),
        text=_join(e.get("purpose"), e.get("usage"),
                   e.get("caution"), e.get("note")),
    )


def _symptom_doc(e: Dict[str, Any]) -> Doc:
    aliases = _aliases(e)
    name = aliases[0] if aliases else ""
    return Doc(
        id=e.get("id") or (name or "symptom"),
        kind=KIND_SYMPTOM,
        key=e.get("id") or (name or "symptom"),
        name=name,
        aliases=aliases,
        source=_source(e),
        text=_join(e.get("text"), e.get("department")),
    )


def _disease_doc(key: str, e: Dict[str, Any]) -> Doc:
    return Doc(
        id=e.get("id") or f"disease_{key}",
        kind=KIND_DISEASE, key=key, name=key,
        aliases=_aliases(e),
        source=_source(e),
        text=_join(e.get("what"), e.get("advice")),
    )


def _department_doc(key: str, e: Dict[str, Any]) -> Doc:
    return Doc(
        id=f"dept_{key}",
        kind=KIND_DEPARTMENT, key=key, name=key,
        aliases=_aliases(e),
        text="",
    )


def build_docs(kb: Dict[str, Any]) -> List[Doc]:
    """把一份知识库展平成文档列表。顺序稳定（按段落与 YAML 顺序）。"""
    docs: List[Doc] = []
    for key, e in (kb.get("lab_tests") or {}).items():
        if isinstance(e, dict):
            docs.append(_lab_doc(key, e))
    for key, e in (kb.get("medications") or {}).items():
        if isinstance(e, dict):
            docs.append(_medication_doc(key, e))
    for e in (kb.get("symptoms") or []):
        if isinstance(e, dict):
            docs.append(_symptom_doc(e))
    for key, e in (kb.get("diseases") or {}).items():
        if isinstance(e, dict):
            docs.append(_disease_doc(key, e))
    for key, e in (kb.get("departments") or {}).items():
        if isinstance(e, dict):
            docs.append(_department_doc(key, e))
    return docs


def doc_text_for_index(d: Doc) -> str:
    """索引正文 = 展示名 + 别名 + **条目正文**。

    ⚠️ **这里的口径与"实体识别"用过的口径不同，是有意的。**

    2026-09-21 早先那次实验（.scratch/rag-ux/index-scope-bench.py）测出
    "索引含正文会带来更多错配"，据此把正文排除在索引之外——但那个结论
    **只适用于"判断用户在问哪个条目"**：那时「我肾怎么样」会因为正文里
    写着"与心、肾…有关"而命中「水肿」，确实错了。

    **本模块的用途不是实体识别，而是为生成提供相关段落**（RAG）。这时
    正文恰恰是**最该被检索的东西**：用户问一句知识库没有直接对应条目的
    话，我们要找的是"哪些条目与这个问题相关"，而不是"这是哪一条"。
    两者的正确索引口径本来就不一样。
    """
    return " ".join((d.name, *d.aliases, d.text))


def aliases_of(docs: Iterable[Doc]) -> List[str]:
    """供分词器建用户词典用。"""
    out: List[str] = []
    for d in docs:
        out.extend(d.aliases)
    return out
