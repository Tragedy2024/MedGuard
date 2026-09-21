"""智慧医生知识库（demo/smart_knowledge.yaml v2）的结构守卫。

与 test_demo_policy.py 对 SSA 策略的做法一致：**漏一个即失败**，
不靠运行时回落悄悄兜底。

为什么这些守卫是必要的：v2 新增的 `aliases` / `relations` / `provenance`
都是**给检索与溯源用的元数据**，缺了不会让程序崩——只会让检索悄悄找不到、
或让来源标注悄悄说谎。这类"静默降级"只能靠测试挡住。
"""
from backend import smart_doctor
from backend.smart_doctor import _KB_VERSION

# 关系类型。`就诊科室` 的 target 指向 `departments` 段的键；
# 其余类型的 target 指向内容条目的 `id`。
DEPT_REL = "就诊科室"
REL_TYPES = {DEPT_REL, "相关检验", "常用药物", "提示疾病"}

# dict 型的内容段（键即条目的规范名）
DICT_SECTIONS = ("lab_tests", "medications", "diseases")


def _kb():
    kb = smart_doctor.load_knowledge()
    assert kb, "知识库未加载（版本守卫或读取失败？）"
    return kb


def _entries(kb):
    """产出 (定位串, 条目) —— 覆盖四种内容条目（symptoms 是 list 型）。"""
    for sec in DICT_SECTIONS:
        for key, entry in (kb.get(sec) or {}).items():
            yield f"{sec}.{key}", entry, key
    for entry in (kb.get("symptoms") or []):
        yield f"symptoms.{entry.get('id', '?')}", entry, None


def test_knowledge_version_is_current():
    """版本号必须是当前代码支持的版本。

    load_knowledge 已对版本做守卫（不符则整体按加载失败处理），这里再钉一次：
    守卫本身失效时应当有测试报警，而不是让 _kb() 的空断言去兜。
    """
    import yaml
    from backend import config
    import os
    path = os.path.join(config.DEMO_DIR, "smart_knowledge.yaml")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    assert raw.get("version") == _KB_VERSION


def test_every_entry_has_stable_id():
    """每个条目必须有 id —— 它是 relations 的引用目标。"""
    for loc, entry, _ in _entries(_kb()):
        assert entry.get("id"), f"{loc} 缺 id"


def test_ids_are_unique():
    """id 必须唯一，否则 relations 的解析结果取决于遍历顺序。"""
    seen = {}
    for loc, entry, _ in _entries(_kb()):
        i = entry.get("id")
        assert i not in seen, f"id 重复：{i}（{seen.get(i)} 与 {loc}）"
        seen[i] = loc


def test_every_entry_has_non_empty_aliases():
    """每个条目必须有非空 aliases —— 这是字面检索的燃料。

    v1 里 diseases 用 keywords、symptoms 用 keys、lab_tests 与 medications
    完全没有，检索层无从下手。v2 统一为 aliases 并强制必填。
    """
    for loc, entry, _ in _entries(_kb()):
        a = entry.get("aliases")
        assert isinstance(a, list) and a, f"{loc} 缺 aliases 或为空"
        for x in a:
            assert isinstance(x, str) and x.strip(), f"{loc} 的 aliases 含空值"


def test_canonical_name_is_always_searchable():
    """dict 型段落的键（条目规范名）必须出现在自己的 aliases 里。

    否则「问的就是它的本名」反而检索不到——这是最容易漏的一种。
    """
    kb = _kb()
    for sec in DICT_SECTIONS:
        for key, entry in (kb.get(sec) or {}).items():
            assert key in (entry.get("aliases") or []), \
                f"{sec}.{key} 的 aliases 里没有它自己的名字"


def test_every_entry_has_provenance():
    """每个条目必须写明来源。

    「医院审核知识库」这六个字是这个产品的诚实底线——它只能出现在
    **真被医院审核过**的条目上。字段缺失时界面会退化成不标注，
    那等于默认所有内容都可信。
    """
    for loc, entry, _ in _entries(_kb()):
        p = entry.get("provenance")
        assert isinstance(p, dict), f"{loc} 缺 provenance"
        assert isinstance(p.get("reviewed"), bool), \
            f"{loc} 的 provenance.reviewed 必须是布尔值"
        assert p.get("source"), f"{loc} 的 provenance 缺 source"


def test_every_relation_target_resolves():
    """relations 的 target 必须解析得到 —— 防悬空边。

    悬空边不会让程序崩：多跳时走不通，只表现为「检索少了一截」，
    排查起来极其费劲。所以在数据层就挡住。
    """
    kb = _kb()
    dept_names = set(kb.get("departments") or {})
    entry_ids = {e.get("id") for _, e, _ in _entries(kb)}

    for loc, entry, _ in _entries(kb):
        for rel in (entry.get("relations") or []):
            rtype = rel.get("type")
            target = rel.get("target")
            assert rtype, f"{loc} 的关系缺 type：{rel}"
            assert target, f"{loc} 的关系缺 target：{rel}"
            if rtype == DEPT_REL:
                assert target in dept_names, \
                    f"{loc} 的就诊科室 {target!r} 不在 departments 段里"
            else:
                assert target in entry_ids, \
                    f"{loc} 的关系目标 {target!r} 不是任何条目的 id"


def test_every_department_alias_is_present():
    """科室条目必须有 aliases —— 它正是用来弥合「心内科 / 心血管内科」的。

    演示业务库写 `心内科`，而知识库的症状条目写 `心血管内科`；没有别名，
    同一个科室在两个界面里就是两个东西。
    """
    for name, d in (_kb().get("departments") or {}).items():
        a = d.get("aliases")
        assert isinstance(a, list) and a, f"科室 {name} 缺 aliases"
        assert name in a, f"科室 {name} 的 aliases 里没有它自己的名字"


def test_symptom_department_resolves_to_department_table():
    """症状的 department 必须能在科室表里落地。

    `心理科/神经内科` 这类**复合值按「/」拆分后分别**校验——它是 v1 原样
    保留的展示文案，会直接渲染给患者（「建议优先咨询心理科/神经内科」），
    不能为了对齐科室表而简化它（那是行为变更）。
    """
    kb = _kb()
    known = set()
    for name, d in (kb.get("departments") or {}).items():
        known.add(name)
        known.update(d.get("aliases") or [])

    for sym in (kb.get("symptoms") or []):
        raw = sym.get("department") or ""
        assert raw, f"{sym.get('id')} 缺 department"
        for part in raw.split("/"):
            assert part.strip() in known, \
                f"{sym.get('id')} 的科室 {part!r} 不在 departments 段里"


def test_relation_types_are_from_a_known_set():
    """关系类型必须来自已知集合，防手滑写出 `就诊科室 ` 这类带空格的变体。"""
    for loc, entry, _ in _entries(_kb()):
        for rel in (entry.get("relations") or []):
            assert rel.get("type") in REL_TYPES, \
                f"{loc} 出现未知关系类型 {rel.get('type')!r}"


def test_no_alias_is_shared_within_the_same_kind():
    """★ 同类条目之间不得共用别名。

    扩写时最容易出的错：两条症状都写上了同一个说法，于是「命中哪一条」
    变成由 YAML 顺序决定的偶然事件——而且改一次顺序，行为就变一次。

    **刻意只管同类**：症状与疾病之间**允许**共用别名，那是有意的——
    「我胃痛」既是症状（导诊）也是疾病的别名（科普），parse_intent 靠
    这条重叠做歧义引导。跨类的重叠是设计，同类的重叠是 bug。
    """
    seen = {}
    for loc, entry, _ in _entries(_kb()):
        kind = loc.split(".")[0]
        for a in (entry.get("aliases") or []):
            key = (kind, a)
            assert key not in seen, \
                f"别名 {a!r} 在同类（{kind}）里被 {seen[key]} 与 {loc} 共用"
            seen[key] = loc


# ── 别名覆盖（口语说法）────────────────────────────────────────

def test_colloquial_aliases_route_correctly():
    """口语说法必须能命中——这正是别名存在的意义。

    2026-09-21 扩充了 7 个口语说法，它们此前**全部**落进"我没听懂"。
    实测见 .scratch/rag-ux/alias-expansion-check.py。
    """
    from backend.smart_doctor import parse_intent
    for q, want in [("查一下肝", "lab"), ("我肾怎么样", "lab"),
                    ("我肚子疼", "symptom"), ("脑袋晕", "symptom"),
                    ("发烧了", "symptom"), ("睡不着", "symptom"),
                    ("心里发慌", "symptom")]:
        assert parse_intent(q)[0] == want, f"{q} → {parse_intent(q)}"


def test_single_char_aliases_do_not_steal_disease_questions():
    """★ 单字别名不得把疾病问题抢走。

    `肝`/`肾` 可以当单字别名，是因为知识库里没有别的"肝/肾"条目；
    `糖` **不可以**——`糖尿病` 含 `糖`，加了之后「糖尿病怎么办」会被
    血糖抢走（parse_intent 的 swallowed 判据是"检验名是否落在疾病名里"，
    而 `血糖` 不在 `糖尿病` 里，挡不住）。

    这条守卫把当时的判断固化下来：谁要加单字别名，先过这里。
    """
    from backend.smart_doctor import parse_intent
    assert parse_intent("糖尿病怎么办")[0] == "disease"
    assert parse_intent("我想了解糖尿病")[0] == "disease"
    assert parse_intent("得了糖尿病要注意什么")[0] == "disease"


def test_disease_name_containing_a_lab_alias_is_not_stolen():
    """★ 疾病名里含某检验项的**别名**时，不能被检验项抢走。

    回归：2026-09-21 扩写后「我甲状腺功能减退」被判成
    `lab · 促甲状腺激素`——因为 parse_intent 的 swallowed 判据比的是检验项的
    **规范名**（促甲状腺激素），而用户在字面上命中的是它的**别名**
    （甲状腺功能）。别名接入字面匹配之后，判据必须一并看别名。

    对称的一半同样重要：**单独问检验项时仍然要进 lab**。
    """
    from backend.smart_doctor import parse_intent
    assert parse_intent("我甲状腺功能减退") == ("disease", "甲状腺功能减退")
    assert parse_intent("甲状腺功能亢进怎么办")[0] == "disease"
    assert parse_intent("我的甲状腺功能正常吗")[0] == "lab"
