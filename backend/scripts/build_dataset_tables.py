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
    1. 先下载 OpenCMKG 的 triples.txt（见 .scratch/knowledge-sources/sources.md）
    2. python scripts/build_dataset_tables.py <triples.txt 路径>

输出：
    backend/demo/symptom_departments.json
    backend/demo/disease_facts.json
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
                neighbors[p[0]].add(p[2])
                neighbors[p[2]].add(p[0])

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
    with io.open(triples_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) != 3:
                continue
            e1, rel, e2 = p
            if rel == "disease_belong_department":
                facts[e1]["dept"] = e2
            elif rel in DISEASE_RELATIONS:
                facts[e1][DISEASE_RELATIONS[rel]].append(e2)

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
                      "disease_count": len(out)},
            "diseases": out}


def build(triples_path: str) -> dict:
    dept_of: dict = {}
    sym_to_diseases: dict = collections.defaultdict(list)

    with io.open(triples_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) != 3:
                continue
            e1, rel, e2 = p
            if rel == "disease_belong_department":
                dept_of[e1] = e2
            elif rel == "disease_has_symptom":
                sym_to_diseases[e2].append(e1)

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
                  "alias_map_count": len(alias_map["auto"]) + len(alias_map["manual"])},
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
