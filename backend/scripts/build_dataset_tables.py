# -*- coding: utf-8 -*-
"""从 OpenCMKG 抽取两张表（构建期工具，不在运行时调用）。

    ① 症状 → 就诊科室      disease_has_symptom + disease_belong_department
    ② 疾病 → 事实卡片      disease_has_symptom / need_check / recommand_drug /
                          common_drug / belong_department / accompany_disease /
                          need_treatment

**为什么要有这个脚本**：这两张表的来源必须是**可复现、可审计**的。
项目书里写"知识库来自开源数据集"，评委要能自己跑一遍验证——所以抽取逻辑
放在代码里，而不是我手写一份 JSON 声称来自某数据集。

**产品层的定位**：这两张表只提供**事实**（有哪些症状、归哪个科、该查什么），
**解读与建议由 AI 生成并单独标注**。事实与建议不混同。

用法：
    1. 下载 OpenCMKG 原始三元组（固定版本，SHA-256 见下）：
         URL：https://raw.githubusercontent.com/RuiqingDing/OpenCMKG/master/triples.txt
         SHA-256：1d6e6242baf20a7c9c2044809a53969ffb73e3248ac9a7911f8b053b06438632
       （下载后先 `sha256sum triples.txt` 比对，确保与生成当前 JSON 的输入一致；
         2026-09-24 起本仓库 JSON 即由该版本生成）
    2. python scripts/build_dataset_tables.py <triples.txt 路径>

输出：
    backend/demo/symptom_departments.json
    backend/demo/disease_facts.json

数据清洗（2026-09-24 新增，回应代码评审 P1）：
    原始三元组含抓取残留（作者署名「驻站医/闫铁…」被当成症状、罗马数字
    「Ⅰ」与缩写「SP」混入药品），由 `_clean_value()` 在读取时统一过滤：
    作者署名黑名单 / 罗马数字残渣 / 无汉字且不在医用缩写白名单的纯符号串 /
    单字串。过滤数与本次生成时间记录在两张表的 `_meta.cleaned_dropped`。
"""
import collections
import io
import json
import os
import sys
from datetime import date

# ── 科室名归一化 ──────────────────────────────────────────────
#
# OpenCMKG 的科室叫法与本院不一致，不映射就会出现「同一个科室两个名字」。
# 实测：不映射时推导准确率 40%，映射后 65%（top1）/ 85%（top3）。
#
# 值为 None 表示**不是具体临床科室，丢弃**（它们会稀释投票结果）。
DEPT_NORMALIZE = {
    "心内科": "心血管内科",
    "骨外科": "骨科",
    "精神科": "心理科",
    "肝病": "消化内科",
    "肛肠科": "消化内科",
    "中医综合": None,
    "儿科综合": None,
    "小儿内科": None,
    "小儿外科": None,
}

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

# 每个症状保留的候选科室数
TOP_N = 5

# ── 疾病事实卡片 ──────────────────────────────────────────────
#
# 疾病科普改成「数据集事实 + AI 解读」之后，这里提供的是**事实部分**：
# 该病有哪些常见症状、归哪个科、该查什么、常用什么药、可能有什么并发症。
# 解读性文字由 AI 生成（单独标注），不在这里自写。
#
# 不含饮食关系（recommand_food / noteat_food / eat_food）——那三类合计
# 约 9 万条边，会把文件从 2.9 MB 撑到 4.6 MB，而信息价值最低。
DISEASE_RELATIONS = {
    "disease_has_symptom": "symptoms",
    "disease_need_check": "checks",
    "disease_recommand_drug": "drugs",
    "disease_common_drug": "drugs",
    "disease_accompany_disease": "complications",
    "disease_need_treatment": "treatments",
}
DISEASE_ITEMS_PER_KIND = 5

META = {
    "source": "OpenCMKG",
    "url": "https://github.com/RuiqingDing/OpenCMKG",
    "license": "仅学术研究，不得商用",
    "derived_from": ["disease_has_symptom", "disease_belong_department", "SameAs"],
    "method": ("症状→疾病→科室 两跳聚合投票；口语映射取 SameAs 一跳（不取传递闭包，"
               "否则 85% 节点会坍缩成一个巨分量）"),
    "note": ("本文件是 OpenCMKG 若干关系的**聚合结果**，不是原始数据。"
             "科室名经归一化映射（见 build_symptom_table.py 的 DEPT_NORMALIZE）；"
             "非临床科室（中医综合等）已丢弃。"
             "口语映射中 manual 那几条是日常说法对应，不含医学判断。"),
}


# ── 口语映射 ──────────────────────────────────────────────────
#
# 患者说的是口语（嗓子疼、尿少、没劲），数据集里是书面名（咽痛、少尿、乏力）。
# 这层映射**不是医学知识，是语言常识**——任何检索系统都需要它。
#
# 绝大部分**从数据集自动推导**：OpenCMKG 的 `SameAs` 边就是同义对齐边。
# 实测 76 个说法里 45 个本身就是数据集的症状名、25 个能靠一跳推定，
# 只剩 6 个需要人工补（见 MANUAL_ALIASES）。
#
# ⚠️ **SameAs 只能取直接一跳，不能求传递闭包**：实测闭合后 85% 的节点
# 会坍缩成一个 14,765 节点的巨分量（"所有词都同义于所有词"），完全不可用。

# 人工补/纠正的少数几条——**纯粹的日常说法对应，不含任何医学判断**。
# 值为 None 表示"数据集里没有对应的通用症状名，不硬凑"。
MANUAL_ALIASES = {
    # 日常说法 → 数据集书面名
    "心里发慌": "心慌",
    "晕得慌": "头晕",
    "睡不好": "失眠",
    "耳朵响": "耳鸣",
    "关节红肿": "关节疼痛",
    "痰中带血": "咯血",
    # 自动映射取错的，人工纠正（原因写在旁边）
    "发热": "发烧",     # 自动选了「小儿发热」，不适用于成人
    "感冒": None,       # 数据集只有「反复感冒/胃肠感冒」，没有通用的，不硬凑
}


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度——用来从多个同义候选里挑最贴近的那个。

    例：「大便干结」的候选是 [便血, 大便干燥]，取第一个会错成「便血」；
    按最长公共子串（「大便干」vs「大便干燥」）就能选对。
    """
    best = 0
    for i in range(len(a)):
        for j in range(i + 1, len(a) + 1):
            if a[i:j] in b:
                best = max(best, j - i)
    return best


def build_alias_map(symptoms: dict, triples_path: str) -> dict:
    """把知识库里的症状说法映射到数据集里的症状名。"""
    neighbors: dict = collections.defaultdict(set)
    with io.open(triples_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) == 3 and p[1] == "SameAs":
                a, b = _clean_value(p[0]), _clean_value(p[2])
                if a and b:
                    neighbors[a].add(b)
                    neighbors[b].add(a)

    # 知识库里的说法从 smart_knowledge.yaml 取
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from backend import smart_doctor
        kb = smart_doctor.load_knowledge()
        aliases = sorted({a for s in (kb.get("symptoms") or [])
                          for a in (s.get("aliases") or [])})
    except Exception as exc:      # noqa: BLE001
        print(f"[警告] 读不到知识库，口语映射跳过：{exc}")
        return {"auto": {}, "manual": {}}

    auto = {}
    for a in aliases:
        if a in symptoms:                     # 本身就是数据集里的名字
            continue
        if a in MANUAL_ALIASES:               # 人工清单优先（含「不映射」）
            continue
        cands = [n for n in neighbors.get(a, ()) if n in symptoms]
        if cands:
            auto[a] = max(cands, key=lambda c: (_lcs_len(a, c), -len(c)))
    return {"auto": auto, "manual": dict(MANUAL_ALIASES)}


DISEASE_META = {
    "source": "OpenCMKG",
    "url": "https://github.com/RuiqingDing/OpenCMKG",
    "license": "仅学术研究，不得商用",
    "derived_from": list(DISEASE_RELATIONS) + ["disease_belong_department"],
    "method": (f"按疾病聚合七种关系的宾语，每类保留前 {DISEASE_ITEMS_PER_KIND} 条；"
               "只保留在数据集中有科室归属的疾病"),
    "note": ("本文件是 OpenCMKG 若干关系的**聚合结果**，不是原始数据。"
             "只提供**事实**（症状/检查/药物/并发症/治疗/科室），"
             "**不含解读性文字**——解读与建议由 AI 生成并单独标注。"
             "含中成药名，属数据集原样内容。"),
}


def build_diseases(triples_path: str) -> dict:
    """疾病 → 事实卡片。"""
    facts: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    dropped = 0
    with io.open(triples_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) != 3:
                continue
            e1, rel, e2 = p
            disease = _clean_value(e1, "disease")
            if not disease:
                dropped += 1
                continue
            if rel == "disease_belong_department":
                value = _clean_value(e2, "dept")
                if value:
                    facts[disease]["dept"] = value
                else:
                    dropped += 1
            elif rel in DISEASE_RELATIONS:
                field = DISEASE_RELATIONS[rel]
                value = _clean_value(e2, field)
                if value:
                    facts[disease][field].append(value)
                else:
                    dropped += 1

    out = {}
    for d, v in facts.items():
        if "dept" not in v:
            continue                    # 没科室的疾病回答不了"挂什么科"，略去
        dept = DEPT_NORMALIZE.get(v["dept"], v["dept"])
        if not dept:
            continue                    # 非临床科室，丢弃
        card = {"dept": dept}
        for k, items in v.items():
            if k == "dept":
                continue
            # 去重保序，截断
            seen, kept = set(), []
            for x in items:
                if x and x not in seen:
                    seen.add(x)
                    kept.append(x)
            if kept:
                card[k] = kept[:DISEASE_ITEMS_PER_KIND]
        out[d] = card

    return {"_meta": {**DISEASE_META, "generated": date.today().isoformat(),
                      "disease_count": len(out), "cleaned_dropped": dropped},
            "diseases": out}


def build(triples_path: str) -> dict:
    dept_of: dict = {}
    sym_to_diseases: dict = collections.defaultdict(list)
    dropped = 0

    with io.open(triples_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) != 3:
                continue
            e1, rel, e2 = p
            disease = _clean_value(e1, "disease")
            if not disease:
                dropped += 1
                continue
            if rel == "disease_belong_department":
                value = _clean_value(e2, "dept")
                if value:
                    dept_of[disease] = value
                else:
                    dropped += 1
            elif rel == "disease_has_symptom":
                value = _clean_value(e2, "symptoms")
                if value:
                    sym_to_diseases[value].append(disease)
                else:
                    dropped += 1

    symptoms = {}
    for sym, diseases in sym_to_diseases.items():
        counter: collections.Counter = collections.Counter()
        for d in diseases:
            raw = dept_of.get(d)
            if not raw:
                continue
            name = DEPT_NORMALIZE.get(raw, raw)
            if name:                       # None = 非临床科室，丢弃
                counter[name] += 1
        if counter:
            symptoms[sym] = {
                "total": len(diseases),
                "depts": [[k, v] for k, v in counter.most_common(TOP_N)],
            }

    alias_map = build_alias_map(symptoms, triples_path)

    return {
        "_meta": {**META, "generated": date.today().isoformat(),
                  "symptom_count": len(symptoms),
                  "alias_map_count": len(alias_map["auto"]) + len(alias_map["manual"]),
                  "cleaned_dropped": dropped},
        "symptoms": symptoms,
        "alias_map": alias_map,
    }


def _write(path: str, data: dict) -> None:
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[完成] {os.path.basename(path):<28} {os.path.getsize(path)/1024/1024:.2f} MB")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    src = sys.argv[1]
    if not os.path.isfile(src):
        sys.exit(f"[错误] 找不到 {src}")

    demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "demo")

    sym = build(src)
    _write(os.path.join(demo, "symptom_departments.json"), sym)
    m = sym["_meta"]
    print(f"       症状 {m['symptom_count']} 个 · 口语映射 {m['alias_map_count']} 条 "
          f"（自动 {len(sym['alias_map']['auto'])} / 人工 {len(sym['alias_map']['manual'])}）")

    dis = build_diseases(src)
    _write(os.path.join(demo, "disease_facts.json"), dis)
    print(f"       疾病 {dis['_meta']['disease_count']} 个")


if __name__ == "__main__":
    main()
