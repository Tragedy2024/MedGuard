"""从固定版本的 OpenCMKG 原始 CSV 生成三份本地知识库文件。

运行 python scripts/build_dataset_tables.py <triples.txt> --export-docs
用 --output-dir 指定暂存目录，用 --review-log 保存待复核记录及原始行号。
"""
import argparse
import collections
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

TRIPLES_SHA256 = "1d6e6242baf20a7c9c2044809a53969ffb73e3248ac9a7911f8b053b06438632"
SOURCE_VERSION = "61ff8aa8635b7b4c4c831cc56108e4116ecc3c01"
SOURCE_URL = f"https://raw.githubusercontent.com/RuiqingDing/OpenCMKG/{SOURCE_VERSION}/triples.txt"

# 仅保留明确的名称归一化。专业科室与院内组织的映射应由院方配置。
DEPT_NORMALIZE = {"心内科": "心血管内科", "骨外科": "骨科"}

# ── 实体清洗 ──────────────────────────────────────────────────
#
# OpenCMKG 是从医学网站爬取后对齐出的图谱，原始三元组里混着**抓取残留**：
# 文章作者署名（「驻站医」「仝超」被当成症状）、列表编号残渣（「Ⅰ」「SP」）、
# 括号残缺的厂商片段（「武汉)医药」「河北)」）、被截断的半句话
# （「脉搏细弱甚至不」）、损坏的疾病名（「N动脉瘤」）。2026-09 代码评审
# P1 与复检均确认这些污染会直接展示给患者或传给 LLM。
#
# 清洗规则取**精度优先**：只丢明确非医学实体的值，不碰可疑但可能是真
# 术语的条目（宁可漏洗，不可误杀）。字段级（field）规则只加在能确定的
# 类别上——例如 `Q热`/`X综合征`/`I型肾小管性酸中毒`/`B链球菌群感染` 是
# 合法疾病名（字母前缀是分型标记），不能按"字母开头"一刀切。

# 已核实的作者署名残渣（出现频次来自 2026-09 审查与复检实测）
_NAME_BLACKLIST = frozenset(
    {"驻站医", "闫铁", "毓卓", "要利琴", "闫鹏辉", "仝超"}
)

# 罗马数字编号残渣（「Ⅰ」「Ⅱ」…）
_ENUM_JUNK = frozenset("ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ")

# 无汉字时只放行的医用缩写；其余纯字母/纯数字/纯符号一律丢弃
_ABBREV_ALLOW = frozenset({
    "CT", "MRI", "PET", "ECG", "EEG", "CRP", "ESR", "AST", "ALT",
    "GOT", "GPT", "GGT", "WBC", "RBC", "PLT", "HB", "HGB", "HCG",
    "PSA", "AFP", "CEA", "TSH", "SPECT", "B超",
})

# 已人工核实的截断/OCR 残片。不能按尾字一刀切：「肌纤维大小不等」、
# 「胃肠道排空表现」都是完整术语，也分别以「等/现」结尾。
_TRUNCATED_BLACKLIST = frozenset({
    "黏稠或脓性痰伴", "脉搏细弱甚至不", "痰黏稠或脓性可",
    "下肢久立时出现", "肝内HDAg仅", "腭舌咽呼吸肌呈",
    "颊部脂肪消失呈", "尿道分泌粘液或", "固定于一侧眼及",
    "闭经-乳溢-不", "颌下腺导管口有", "颈根部斜方肌及",
    "腕关节内积血及", "鼻中隔向一侧或", "烧伤创面暗灰或",
    "胎盘母体面上有", "鼻塞排出脓性或", "鼻尖或鼻翼出现",
    "消化道内毛石或", "上腹及腰背部有", "眼底发现黄斑呈",
    "口角和鼻周出现", "产后下腹坠痛或", "难免流产或不可",
    "表皮全层坏死及", "尿道内虫咬感或", "血管壁及周围有",
    "阴道流出黄色或", "老年男性乳房无", "乳头溢出血性或",
    "左侧卧时右腰呈", "左上腹肿块伴有", "皮肤血管收缩呈",
    "自动Babin", "鼻Z", "双眼Bell现", "N动脉搏动减弱或消失",
    "月经时多时少且", "产后小便频数或", "皮下沉积所出现", "左下腹部可触及",
    "食管左壁形成压足E", "皮肌炎Gott", "毛发呈脱发祥",
})

# 字段错位：这些值在上游被标成 symptom，但实际是药物类别/病毒/检查项。
# 只做已核实的精确过滤，避免把「HIV感染」等合法临床表现一并删掉。
_SYMPTOM_BLACKLIST = frozenset({
    "中枢神经抑制药", "HBV与HCV", "白细胞计数(WBC)",
})

# 上游 OCR 将应有的汉字替换成 N；只列已确认的七个疾病名，避免误删
# 合法的字母分型疾病。
_DISEASE_BLACKLIST = frozenset({
    "N肌腱损伤", "N血管陷迫综合征", "N动脉瘤", "N动脉破裂",
    "N动脉损伤", "N肌肌腱炎", "N动脉陷迫综合征",
})


def _clean_value(v: str, field: str = "generic") -> str:
    """清洗一个医学实体值；返回空串表示丢弃。零 IO、确定性、可单测。

    field 取 disease / symptoms / checks / drugs / complications /
    treatments / dept / generic——字段级规则只加在能确定类别的场景。
    """
    if v is None:
        return ""
    s = v.strip()
    if not s:
        return ""
    if s in _NAME_BLACKLIST:
        return ""
    if s in _TRUNCATED_BLACKLIST:
        return ""
    if len(s) == 1 and s in _ENUM_JUNK:
        return ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in s):
        # 纯非汉字串：只放行医用缩写白名单
        return s if s in _ABBREV_ALLOW else ""
    if len(s) < 2:
        return ""

    # 括号必须配对（半角与全角分开计）：厂商片段「武汉)医药」、
    # 残缺症状「红斑(边界清楚」都以括号不配对为特征
    if s.count("(") != s.count(")") or s.count("（") != s.count("）"):
        return ""

    if field == "disease":
        if s in _DISEASE_BLACKLIST:
            return ""
    elif field == "symptoms":
        if s in _SYMPTOM_BLACKLIST:
            return ""
    return s

DEPARTMENT_RELATION = "disease_belong_department"
DISEASE_RELATIONS = {
    "disease_has_symptom": "symptoms",
    "disease_need_check": "checks",
}
REVIEW_ONLY_RELATIONS = {
    "SameAs": "实体对齐待复核",
    "disease_acompany_disease": "并发关系待复核",
    "disease_common_drug": "用药关系待复核",
    "disease_recommand_drug": "网页药品展示关系待复核",
    "disease_need_treatment": "治疗关系待复核",
}
OUT_OF_SCOPE_RELATIONS = {
    "department_belong_department", "disease_noteat_food", "disease_eat_food",
    "disease_recommand_food", "drug_relate_producer",
}

# 仅记录明确的常见写法。匹配时还要确认目标确实在本次生成结果中。
MANUAL_ALIASES = {
    "心里发慌": "心慌", "发慌": "心慌", "晕得慌": "头晕", "脑袋晕": "头晕",
    "睡不着": "入睡困难", "睡不好": "失眠", "耳朵响": "耳鸣", "嗓子疼": "咽痛",
    "肚子疼": "腹痛", "肚子痛": "腹痛", "胃疼": "胃痛",
    "胸口疼": "胸痛", "腰疼": "腰痛", "尿少": "少尿",
    "皮肤痒": "皮肤瘙痒", "大便带血": "便血", "血便": "便血",
    "看东西模糊": "视物模糊", "视力模糊": "视物模糊",
    "大便干结": "大便干燥", "流涕": "流鼻涕",
    "发热": "发烧", "感冒": None, "起疹子": "皮疹",
}


def _read_source(path, log=None):
    cards = collections.defaultdict(lambda: collections.defaultdict(dict))
    counts = collections.Counter()

    def exclude(line, reason, raw):
        counts[reason] += 1
        if log is not None:
            log.write(json.dumps({"line": line, "reason": reason, "raw": raw},
                                 ensure_ascii=False) + "\n")

    with open(path, encoding="utf-8", newline="") as stream:
        for line_no, raw in enumerate(stream, 1):
            try:
                cells = next(csv.reader([raw], strict=True))
                if len(cells) != 3 or not all(cell.strip() for cell in cells):
                    raise ValueError("应为三个非空字段")
            except (csv.Error, ValueError):
                exclude(line_no, "格式错误或空字段", raw.rstrip("\r\n"))
                continue
            head, relation, tail = (cell.strip() for cell in cells)
            if relation in REVIEW_ONLY_RELATIONS:
                exclude(line_no, REVIEW_ONLY_RELATIONS[relation], cells)
                continue
            if relation in OUT_OF_SCOPE_RELATIONS:
                counts["本次未使用的关系"] += 1
                continue
            field = "dept" if relation == DEPARTMENT_RELATION else DISEASE_RELATIONS.get(relation)
            if field is None:
                exclude(line_no, "未知关系", cells)
                continue
            disease = _clean_value(head, "disease")
            value = _clean_value(tail, field)
            if not disease or not value or "\ufffd" in disease or "\ufffd" in value:
                exclude(line_no, "命中清洗规则", cells)
                continue
            # dict 保持源顺序。重复记录保留第一次出现的原始行。
            cards[disease][field].setdefault(value, line_no)
    return cards, dict(counts)


def _metadata(stats):
    return {"source": "OpenCMKG", "url": SOURCE_URL, "source_version": SOURCE_VERSION,
            "sha256": TRIPLES_SHA256, "license": "仅学术研究，不得商用",
            "review_status": "未经临床复核", "generated": date.today().isoformat(),
            "cleaned_dropped": stats.get("命中清洗规则", 0), "processing_counts": stats}


def _make_tables(cards, stats):
    diseases = {}
    symptom_links = collections.defaultdict(list)
    for disease, fields in cards.items():
        original_depts = list(fields.get("dept", {}))
        depts = list(dict.fromkeys(DEPT_NORMALIZE.get(d, d) for d in original_depts))
        card = {field: list(fields[field]) for field in DISEASE_RELATIONS.values() if field in fields}
        if depts:
            card["departments"] = depts
            card["source_departments"] = original_depts
            if len(depts) == 1:
                card["dept"] = depts[0]
        card["source_lines"] = {field: list(values.values()) for field, values in fields.items()}
        diseases[disease] = card
        for symptom in card.get("symptoms", []):
            symptom_links[symptom].append(disease)

    symptoms = {}
    for symptom, linked in symptom_links.items():
        counts = collections.Counter(
            diseases[d]["dept"] for d in linked if "dept" in diseases[d]
        )
        if counts:
            symptoms[symptom] = {
                "total": len(linked),
                "depts": sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5],
            }
    manual = {name: target for name, target in MANUAL_ALIASES.items()
              if target is None or (target in symptoms and name != target)}
    return (
        {"_meta": {**_metadata(stats), "disease_count": len(diseases)},
         "diseases": diseases},
        {"_meta": {**_metadata(stats), "symptom_count": len(symptoms),
                   "alias_map_count": len(manual)},
         "symptoms": symptoms, "alias_map": {"auto": {}, "manual": manual}},
        symptom_links,
    )


def _make_documents(disease_table, symptom_table, symptom_links):
    documents = []
    for disease, card in disease_table["diseases"].items():
        parts = []
        for key, label in (("symptoms", "关联症状"), ("checks", "关联检查")):
            if card.get(key):
                parts.append(label + "：" + "、".join(card[key]))
        if card.get("dept"):
            parts.append("就诊科室：" + card["dept"])
        documents.append(_document("disease", disease, parts))
    disease_count = len(documents)
    for symptom, linked in symptom_links.items():
        parts = ["关联疾病：" + "、".join(linked[:50]) + ("等" if len(linked) > 50 else "")]
        if symptom in symptom_table["symptoms"]:
            depts = symptom_table["symptoms"][symptom]["depts"]
            parts.append("可能就诊科室：" + "、".join(dept for dept, _ in depts))
        documents.append(_document("symptom", symptom, parts))
    return {"_meta": {**disease_table["_meta"], "doc_count": len(documents),
                      "disease_docs": disease_count, "symptom_docs": len(documents) - disease_count},
            "documents": documents}


def _document(kind, name, parts):
    return {"id": f"opencmkg:{kind}:{name}", "kind": kind, "key": name, "name": name,
            "aliases": [name], "text": "；".join(parts), "source_version": SOURCE_VERSION}


def build_diseases(triples_path):
    return _make_tables(*_read_source(triples_path))[0]


def build(triples_path):
    return _make_tables(*_read_source(triples_path))[1]


def build_retrieval_docs(triples_path):
    return _make_documents(*_make_tables(*_read_source(triples_path)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("triples", type=Path)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "demo")
    parser.add_argument("--export-docs", action="store_true")
    parser.add_argument("--review-log", type=Path)
    args = parser.parse_args()

    with args.triples.open("rb") as raw:
        sha256 = hashlib.file_digest(raw, "sha256").hexdigest()
    if sha256 != TRIPLES_SHA256:
        parser.error(f"原始文件 SHA-256 不符，实际为 {sha256}")

    if args.review_log:
        args.review_log.parent.mkdir(parents=True, exist_ok=True)
        with args.review_log.open("x", encoding="utf-8") as log:
            cards, stats = _read_source(args.triples, log)
    else:
        cards, stats = _read_source(args.triples)
    diseases, symptoms, links = _make_tables(cards, stats)
    outputs = {"disease_facts.json": diseases, "symptom_departments.json": symptoms}
    if args.export_docs:
        outputs["rag_documents.json"] = _make_documents(diseases, symptoms, links)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        target = args.output_dir / name
        temporary = target.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(target)
        print(target)


if __name__ == "__main__":
    main()
