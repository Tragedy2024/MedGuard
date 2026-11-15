"""智慧医生——面向患者的"可信就医助手"（智慧医生.docx）。

职责：理解患者意图 → 经医盾管线（层一准入 + 层二审计）只读**本人**数据
→ 用医院审核知识库（demo/smart_knowledge.yaml）产出三块内容：
  1. 院内数据    （来源：医院主库）——查询计划照常走层一/层二
  2. 通俗解读    （来源：医院审核知识库）
  3. 下一步建议  （来源：医院审核知识库）

知识库未覆盖的问法不会交给模型凭记忆自由回答。RAG 先取回可追溯证据，
模型只能把证据改写成通俗中文；每条主张还要通过证据 ID、数字与药品名的
确定性门禁。无证据、未配置 API Key、调用失败或校验失败时，回退到原始
证据或确定性引导文案，**绝不 500**。

关键设计：
- **医盾管线完全复用**：智慧医生和查询控制台都只能调用
  `routers.query.run_audited_plan`。层一确保"只能读取本人"，层二确保中间
  结果不泄露；执行前还校验 SQL 哈希，并把完整决策落入安全报告。
  自由提问（/api/query/direct）缓存里的计划同样适用——本模块构造的计划
  目标是验证过的物理名，直接构造即可。
- **知识库优先，AI 兜底**：知识库是人工审核的确定性内容（离线、毫秒级）；
  只有知识库没覆盖的问法才交给 AI。涉及本人数据的意图（lab / medication）
  永远不把数据发给模型——AI 只做解释，不碰数据。
- **实体识别靠别名，不靠检索** ★：`_find_entity` / `_find_entities` /
  `_find_disease` 把「规范名 + 别名」一起做**字面匹配**。
  2026-09-21 实现过一版 BM25 检索兜底（jieba 分词 + 手写 BM25 + 图扩跳），
  **实测增量为零**，已删除。理由是结构性的、与语料规模无关：

      索引 token 全部来自别名 → 而别名又都在分词用户词典里（每个别名
      是一个整 token）→ 于是「检索能命中」⟺「某别名是查询的子串」，
      那正是字面匹配的条件；而字面匹配先跑，兜底永远没有机会。

  两种能让它触发的改法实测都**引入错配**：去掉用户词典 →
  「血压有点高」命中「高血脂」（单字「高」重合）；把正文加进索引 →
  「我肾怎么样」命中「水肿」（正文里写着"与心、肾…有关"）。
  实测脚本与数据见 `.scratch/rag-ux/`（该目录 gitignore）。

  → **要提升覆盖率，请加别名，不要加检索。** 13 个漏掉的问法
  （`发烧`↔`发热`、`睡不着`↔`失眠`、`肚子疼`↔`腹痛`）全部是词本身
  对不上——字面检索跨不过同义词，只有别名能。
"""
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import yaml

from backend import config
from backend import dataset_kb
from backend.redflags import check as redflag_check
from backend.labels import DEGRADATION_MESSAGES
from backend.schemas import Token

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

# 知识库格式版本。载入时强校验（见 load_knowledge）——v1 与 v2 的条目字段名
# 不同（keywords/keys → aliases），读错了不会报错，只会静默失去全部关键字。
_KB_VERSION = 2


# ── 分诊等级（acuity）────────────────────────────────────────
#
# 对齐国内「三区四级」（WS/T 390-2012《医院急诊科规范化流程》）：
#   1 濒危（即刻处置）／2 危重（10 分钟内）／3 急症（30 分钟内）／4 非急症
# 只有 1、2 级弹急诊横幅。3 级（如需尽快复诊的检验显著异常）不弹——把"人人
# 弹警告"压下去，真急症信号才不会被稀释。
#
# 这套分级**替代**了原先「知识库条目里有没有 urgent_text」的判定。那是个
# 语义错误：urgent_text 属于 Schmitt-Thompson 体系里的 safety-net 提示
# （"若同时出现…请立即就医"），本来就该无条件展示，不表示本次回答紧急。
_ACUITY_URGENT_MAX = 2
_DEFAULT_ACUITY = 4
_ACUITY_LABELS = {1: "即刻", 2: "危重", 3: "急症", 4: "非急症"}

# 单次模型调用的等待上限（秒）。见 _call_llm 的说明。
_LLM_TIMEOUT_SEC = 30


def _acuity(value: Any) -> int:
    """归一成 1–4。缺失或非法值按 4（非急症）处理。

    知识库条目**应当**显式声明 acuity；默认值是给"漏写"兜底的，不是设计
    意图——tests 里有一条用例会遍历 symptoms 断言每条都写了。
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_ACUITY
    return n if 1 <= n <= 4 else _DEFAULT_ACUITY


def _text(value: Any, default: str = "") -> str:
    """取知识库的字符串字段。

    YAML 里写了键而值为 null 时，`dict.get(k, default)` 返回的是 None 而非
    default（默认值只在**键缺失**时才生效）。None 喂给 pydantic 的 str 字段
    会直接 500，喂给 re.split 会 TypeError。知识库是人工维护的，"先留空待补"
    是很自然的写法，必须兜住。
    """
    return value if isinstance(value, str) and value else default


def _as_bool(value: Any) -> bool:
    """严格布尔归一。

    模型常把布尔输出成字符串，而 `bool("false")` 是 True——那会让非紧急
    回答弹出急诊横幅（假警报）。字符串按白名单判真，其余一律 False。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "是")
    if isinstance(value, (int, float)):
        return value != 0
    return False


# ── 知识库加载 ────────────────────────────────────────────────

def load_knowledge() -> Dict[str, Any]:
    """加载医院审核知识库（进程内缓存，文件损坏时返回空结构）。"""
    global _KB
    # 读缓存与写缓存放在同一把锁里：原先的"锁外读 + 锁内写"两个请求会同时
    # 判定 _KB is None 并各加载一次。
    with _LOCK:
        if _KB is not None:
            return _KB
        path = os.path.join(config.DEMO_DIR, "smart_knowledge.yaml")
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            # 读取失败**不缓存**：一次瞬时的 OSError 若把空结构永久留下来，
            # 之后每次 parse_intent / interpret_lab 都拿到空知识库，直到
            # 进程重启才恢复，而且没有任何重试路径。
            return {}
        # 格式版本守卫。v1 用 keywords/keys、条目没有 aliases，被 v2 的代码
        # 读到时会**静默失去全部关键字**——症状与疾病从此再也匹配不上，
        # 却没有一行报错。这类静默降级正是本项目最反对的失败模式，故显式挡住。
        # 与"读取失败"同样处理：不缓存、返回空结构、打一行可读的原因。
        if data.get("version") != _KB_VERSION:
            print(f"[smart-doctor] 知识库版本不符：期望 {_KB_VERSION}，"
                  f"实际 {data.get('version')!r}。按加载失败处理，"
                  f"请检查 {path}", flush=True)
            return {}
        _KB = data
        return _KB


# ── 意图识别（确定性关键词，零 LLM） ──────────────────────────

_ASK_DEPT = ("挂什么科", "看什么科", "挂哪个科", "该挂", "挂科", "看哪个科",
             "去哪个科", "什么科室")
_MED_SIGNALS = ("怎么吃", "怎么服用", "用法", "用量", "吃法", "服药", "吃药",
                "服用")
_LAB_SIGNALS = ("报告", "结果", "检查", "化验", "指标", "正常吗", "正常么",
                "参考范围", "检测")
_DISEASE_SIGNALS = ("想了解", "是什么病", "这种病", "这个病", "了解一下",
                    "科普")
# 2026-10 医学安全评测（C7）发现：「颈椎病一般做什么检查」被 _LAB_SIGNALS
# 的「检查」抢成检验意图。这类**指向疾病的检查措辞**归疾病意图（带数据集
# 扫描），单独的「我想做个体检」不受影响（无疾病名命中时仍走原路径）。
_DISEASE_CHECK_SIGNALS = ("做什么检查", "做哪些检查", "怎么检查",
                          "该做什么检查", "需要做什么检查")
# 只在问题里同时出现数据集的完整疾病名时生效；不会把单独的「我有哪些症状」
# 猜成某种疾病。用于覆盖「帕金森病的早期表现」这类事实卡问法。
_DISEASE_FACT_SIGNALS = ("早期表现", "常见表现", "常见症状", "有哪些症状",
                         "什么症状", "如何治疗", "怎么治疗", "治疗方法",
                         "有哪些并发症", "常见并发症")


def _names_of(key: str, entry: Any) -> List[str]:
    """一个条目可被问到的全部名字：规范名（YAML 键）+ 别名。"""
    names = [key] if key else []
    if isinstance(entry, dict):
        names.extend(a for a in (entry.get("aliases") or [])
                     if isinstance(a, str) and a)
    return names


def _find_entity(question: str, table: Dict[str, Any]) -> Optional[str]:
    """在知识库某表里找命中的条目，返回它的**键**（如 lab_tests 的「血糖」）。

    匹配口径是**规范名与别名一起看**，取最长命中：名字之间有包含关系
    （「血脂」是「高血脂」的子串），短名先命中会让长名永远排不上。

    别名必须参与匹配——否则语料里写的 `空腹葡萄糖`、`转氨酶` 这些同义
    说法全是死数据，患者换个说法就掉进"我没听懂"（实测：接入前这四个
    问法全部答不上来，接入后全部命中）。
    """
    best_len, best_key = 0, None
    for key, entry in table.items():
        for n in _names_of(key, entry):
            if n in question and len(n) > best_len:
                best_len, best_key = len(n), key
    return best_key


def _find_entities(question: str, table: Dict[str, Any]) -> List[str]:
    """问题里命中的**全部**条目（返回键），长的在前；被更长命中包住的丢掉。

    「我的血脂和血糖结果正常吗」原先只查血糖（YAML 顺序在前），血脂完全
    不查也不提，患者拿到的是误导性的安心答复。
    """
    matched: List[Tuple[str, str]] = []          # (命中的名字, 键)
    for key, entry in table.items():
        best = ""
        for n in _names_of(key, entry):
            if n in question and len(n) > len(best):
                best = n
        if best:
            matched.append((best, key))

    names = [n for n, _ in matched]
    kept = [(n, k) for n, k in matched
            if not any(n != o and n in o for o in names)]
    kept.sort(key=lambda x: len(x[0]), reverse=True)
    out: List[str] = []
    for _, k in kept:
        if k not in out:
            out.append(k)
    return out


def _find_disease(question: str, diseases: Dict[str, Any]
                  ) -> Optional[Tuple[str, str]]:
    """找命中的疾病，返回 (命中的关键词, 疾病名)；取最长命中。

    关键词要一并返回（而不只是疾病名）：上层用它判断"这个命中词是不是
    同时也是个症状词"（「我胃痛」该走导诊而不是疾病科普），也用它生成
    转向引导句。
    """
    hits: List[Tuple[str, str]] = []
    for dname, d in diseases.items():
        for k in (d.get("aliases") or []):
            if k and k in question:
                hits.append((k, dname))
        if dname in question:
            hits.append((dname, dname))
    return max(hits, key=lambda h: len(h[0])) if hits else None


def _symptom_key_matched(keyword: str, symptoms: List[Dict[str, Any]]) -> bool:
    """keyword 是否同时是某个症状条目的 keys（歧义判定）。"""
    return any(keyword in (s.get("aliases") or []) for s in symptoms)


def _disease_of_keyword(keyword: str, diseases: Dict[str, Any]) -> Optional[str]:
    """哪个疾病把 keyword 也当成自己的关键词（生成歧义引导句用）。"""
    for dname, d in diseases.items():
        if keyword in (d.get("aliases") or []):
            return dname
    return None


def parse_intent(question: str) -> Tuple[str, Optional[str]]:
    """返回 (intent, entity)。entity 为命中的具体检验项/药名/症状关键词/疾病名。

    优先级（写成显式顺序，避免歧义）：
      挂科意图 > 用药意图 > 疾病意图词 > 报告解读（避开被**疾病名**包住的假命中）
      > 疾病了解（命中词同时是症状词时转症状导诊） > 症状 > fallback
    """
    kb = load_knowledge()
    lab_tests = kb.get("lab_tests") or {}
    medications = kb.get("medications") or {}
    diseases = kb.get("diseases") or {}
    symptoms = kb.get("symptoms") or []

    # 0) 明确要挂科 → symptom（感冒了该挂什么科，不能落到 disease）
    if any(s in question for s in _ASK_DEPT):
        for sym in symptoms:
            for k in (sym.get("aliases") or []):
                if k and k in question:
                    return INTENT_SYMPTOM, k
        return INTENT_SYMPTOM, None

    # 1) 用药（命中药名或出现"怎么吃/用法"信号）
    drug = _find_entity(question, medications)
    if drug:
        return INTENT_MEDICATION, drug
    if any(s in question for s in _MED_SIGNALS):
        return INTENT_MEDICATION, None

    disease_hit = _find_disease(question, diseases)
    disease_signaled = any(s in question for s in _DISEASE_SIGNALS)
    disease_check_signaled = any(s in question for s in _DISEASE_CHECK_SIGNALS)
    disease_fact_signaled = any(s in question for s in _DISEASE_FACT_SIGNALS)

    # 2) 出现「想了解 / 是什么病 / 科普」这类**疾病意图词**（或「做什么检查」
    #    这类指向疾病的检查措辞）时，疾病优先。否则「我想了解高血脂」会被
    #    lab_tests 的「血脂」抢走。
    #    YAML 疾病优先；未命中时扫描数据集疾病名（7495 个，规范名精确
    #    最长匹配、不模糊匹配）——补上「帕金森病」「颈椎病」这类常见病问法
    #    （评审 A1）。
    if disease_signaled or disease_check_signaled or disease_fact_signaled:
        if disease_hit:
            return INTENT_DISEASE, disease_hit[1]
        dataset_name = dataset_kb.match_disease(question)
        if dataset_name:
            return INTENT_DISEASE, dataset_name

    # 3) 检查报告（命中检验项名，或出现"报告/结果/正常吗"等信号）
    tests = _find_entities(question, lab_tests)
    if tests:
        # 命中的检验项若被**疾病名本身**包住，那是假命中：「高血脂」里的
        # 「血脂」不该让患者去查 test_name='血脂' 的记录——那样只会得到
        # 「未找到检验记录」，而疾病词条永远取不到。
        #
        # 判据必须是"这个关键词就是某个病的名字"。一刀切按"被更长的关键词
        # 包住"来判会误伤：「血糖高」只是 糖尿病 的一个 keyword，患者问
        # 「我的血糖高吗」是在问自己的数值，却会被判成糖尿病科普、
        # 一条本人数据都不取。
        keyword = disease_hit[0] if disease_hit else ""
        # 判据要用该检验项的**全部名字**（规范名 + 别名），不能只用规范名。
        # 例：用户问「我甲状腺功能减退」，检验项是通过别名「甲状腺功能」命中的，
        # 而它的规范名「促甲状腺激素」并不在这个疾病名里——只用规范名会判成
        # "没被包住"，于是检验项把疾病问题抢走，患者拿到一份跟他问的无关的化验。
        # （这条在别名接入之前是对的：那时能命中的只有规范名本身。）
        swallowed = keyword in diseases and any(
            name in keyword
            for t in tests
            for name in _names_of(t, lab_tests.get(t) or {})
        )
        if not swallowed:
            return INTENT_LAB, tests[0]
    if any(s in question for s in _LAB_SIGNALS):
        return INTENT_LAB, None

    # 4) 疾病了解。命中的词若**同时是症状词**（胃痛/咳嗽/感冒…）→ 判为歧义：
    #    患者说"我胃痛"多半是在描述自己的症状，而不是在打听「胃炎」这个病，
    #    故走症状导诊；ask() 会再补一句转向疾病科普的引导。
    if disease_hit:
        keyword, dname = disease_hit
        if _symptom_key_matched(keyword, symptoms):
            return INTENT_SYMPTOM, keyword
        return INTENT_DISEASE, dname
    if any(s in question for s in _DISEASE_SIGNALS):
        return INTENT_DISEASE, None

    # 5) 症状
    for sym in symptoms:
        for k in (sym.get("aliases") or []):
            if k and k in question:
                return INTENT_SYMPTOM, k
    if any(s in question for s in ("不舒服", "症状", "哪里难受")):
        return INTENT_SYMPTOM, None

    return INTENT_FALLBACK, None


# ── 医盾管线取数（只读本人） ──────────────────────────────────

def _build_lab_plan(subject: str, test_names: List[str], limit: int = 3) -> List[dict]:
    """构造读取本人检验结果的计划。test_names 为空表示不限检验项。

    test_names 取自知识库的键（可信 YAML），不是用户原文，故直接拼入 SQL
    与原先一致；值本身不含引号，且层一仍会做 AST 绑定校验。
    """
    label = "、".join(test_names)
    where = ""
    if test_names:
        quoted = ", ".join(f"'{t}'" for t in test_names)
        where = f"AND c.test_name IN ({quoted})"
    return [{
        "id": 0,
        "description": f"读取本人最近检验结果{('（' + label + '）') if label else ''}",
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


@dataclass(frozen=True)
class PersonalDataResult:
    admission: Dict[str, Any]
    degradation: Dict[str, Any]
    columns: Optional[List[str]]
    rows: List[List[Any]]
    sql_after: str
    report_id: Optional[int] = None
    trace_id: str = ""

    def __iter__(self):
        """保留原有五元组解包接口，报告信息通过属性读取。"""
        yield self.admission
        yield self.degradation
        yield self.columns
        yield self.rows
        yield self.sql_after


def fetch_personal_data(
    plan: List[dict], subject: str, *, question: str = "",
    token: Optional[Token] = None, datasource_id: str = config.DEMO_DATASOURCE_ID,
    persist: bool = False,
) -> PersonalDataResult:
    """经统一审计—执行 seam 读取本人数据。

    Returns:
        ``PersonalDataResult``；为兼容旧调用仍可按原五元组解包，报告编号与
        追踪号通过 ``report_id`` / ``trace_id`` 属性读取。
    - admission_dict / degradation_dict：与查询路由同构，前端可直接复用
    - 层一拒绝时 columns=None（不发查询、不执行）
    - sql_after：层二改写后的最终 SQL（展示"医盾拦了什么"的证据）
    - report_id：生产路由持久化后的安全报告编号；纯函数测试为 None
    - trace_id：审计决策编号；层一直接拒绝时为空

    这里不再自行审计或执行 SQL。查询控制台与智慧医生共用
    ``routers.query.run_audited_plan``，因此空审计计划、哈希绑定、数据源
    就绪门禁与报告落盘只有一份实现。
    """
    from backend.routers.query import run_audited_plan

    effective_token = token or Token(
        type="patient", subject_id=subject, account=None)
    response, decision, report_id = run_audited_plan(
        plan, question or "智慧医生读取本人记录", effective_token,
        datasource_id, persist=persist)

    adm = response.admission.model_dump()
    deg = response.degradation.model_dump()
    final_sql = response.plan[-1].sql_after if response.plan else ""
    trace_id = decision.decision_id if decision else ""
    if response.result is None:
        return PersonalDataResult(
            adm, deg, None, [], final_sql, report_id, trace_id)
    return PersonalDataResult(
        adm, deg, response.result.columns, response.result.rows,
        final_sql, report_id, trace_id)


def _unavailable(adm: Dict[str, Any], deg: Dict[str, Any], what: str
                 ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """取不到数据时的解读与建议，按**成因**分别给文案。

    columns 为 None 有三种来源，原先被一股脑当成层一拒绝，于是演示库缺失
    这类基础设施故障也会被播报成「该查询涉及其他患者信息」——一次普通报错
    变成了对患者的越权指控，响应体还自相矛盾（passed=true 却说涉及他人）。
    """
    if not adm.get("passed"):
        text = adm.get("reason") or DEGRADATION_MESSAGES["L3"]
    elif deg.get("level") == "L3":
        text = deg.get("message_cn") or "该查询未通过安全审计，无法提供。"
    else:
        text = f"暂时无法读取您的{what}，请稍后重试或到查询控制台确认。"
    return (
        {"source": _SOURCE_KB, "title": f"无法读取您的{what}",
         "text": text, "items": []},
        {"source": _SOURCE_KB, "text": "如有疑问请联系医院信息科。",
         "actions": [], "urgent": False},
    )


# ── 解读与建议（知识库） ──────────────────────────────────────

def _parse_number(text: str) -> Optional[float]:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def interpret_lab(test_name: str, value_text: str) -> Optional[Dict[str, Any]]:
    """按数值分档解读一个检验结果。

    返回 {label, text, acuity} 或 None（知识缺失）。acuity 来自命中的档位。
    """
    kb = load_knowledge()
    entry = (kb.get("lab_tests") or {}).get(test_name)
    if not entry:
        return None
    value = _parse_number(value_text or "")
    if value is None:
        return {"label": "已出结果", "acuity": _DEFAULT_ACUITY,
                "text": "该结果已出，具体解读请结合临床由医生判读。"}
    # 档位从上到下，第一个「下界与上界同时满足」的生效
    for r in entry.get("ranges") or []:
        lo = r.get("min")
        hi = r.get("max")
        if (lo is None or value >= lo) and (hi is None or value <= hi):
            return {"label": r.get("label", ""), "text": r.get("text", ""),
                    "acuity": _acuity(r.get("acuity"))}
    return {"label": "已出结果", "acuity": _DEFAULT_ACUITY,
            "text": "该结果已出，具体解读请结合临床由医生判读。"}


# ── AI 兜底（知识库未覆盖的问法） ─────────────────────────────
#
# 与 NL→SQL 翻译共用项目自有的 OpenAI 兼容接口适配器。
# 只用于**咨询类**问法（症状/疾病/未识别）；lab 与 medication 涉及
# 本人数据读取，永远不把数据发给模型——AI 只做解释，不碰数据。
# 未配置 Key / 调用失败 / 输出无法解析 → 返回 None，由调用方回退引导。

_AI_SYSTEM = """你是医疗知识证据的通俗转述助手。

要求：
1. 只能转述系统提供的证据，不得使用模型记忆补充医学事实。
2. 不得编造患者本人的检查数据、诊断结果或用药记录。
3. 不替患者下诊断结论；证据不足时必须承认不足。
4. 紧急程度由系统的确定性红旗规则负责，模型不得自行判断。"""


def _call_llm(prompt: str) -> str:
    """调用项目自有的模型接口适配器，并限制用户可见等待时间。"""
    from backend.llm_client import call_llm

    box: List[Any] = []

    def _run() -> None:
        try:
            box.append((None, call_llm(prompt, timeout=_LLM_TIMEOUT_SEC)))
        except BaseException as exc:  # noqa: BLE001 - 原样带回调用方
            box.append((exc, None))

    worker = threading.Thread(target=_run, daemon=True,
                              name="smart-doctor-llm")
    worker.start()
    worker.join(_LLM_TIMEOUT_SEC)
    if worker.is_alive():
        raise TimeoutError(f"模型调用超过 {_LLM_TIMEOUT_SEC}s 未返回")
    err, value = box[0]
    if err is not None:
        raise err
    return value


def _parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出里提取 JSON 对象（容忍围栏、前后废话、脏文本）。"""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    # 数括号时必须跳过 JSON 字符串字面量：字符串里出现**不成对**的
    # { 或 }（如 "血糖 {空腹"）会让深度提前归零 / 一直不归零，导致合法
    # JSON 被判失败，退化成把原始 JSON 原文展示给患者。
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    import json
                    data = json.loads(text[start:i + 1])
                    return data if isinstance(data, dict) else None
                except (ValueError, TypeError):
                    return None
    return None


def _retrieve_context(question: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """从知识库里检索与问题相关的条目 —— **RAG 的检索（R）那一步**。

    纯本地 BM25，**这一步不调模型**。检索不可用时返回空列表：知识库检索
    失败不该连累整个问答，没有上下文就退回原来的纯生成路径。

    注意与实体识别的区别：那里问"用户在问**哪一条**"（用别名精确匹配），
    这里问"哪些条目的**内容**与问题相关"——所以索引含正文，见
    backend/knowledge/corpus.py 的 doc_text_for_index。
    """
    try:
        from backend.knowledge import corpus, hybrid
        kb = load_knowledge()
        if not kb:
            return []
        hits = hybrid.search(kb, question, top_k=top_k)
    except Exception as exc:      # noqa: BLE001 - 检索失败不连累问答
        print(f"[smart-doctor] 知识库检索不可用，退回无上下文作答：{exc}",
              flush=True)
        return []

    refs: List[Dict[str, Any]] = []
    for doc, score in hits:
        refs.append({
            # `ref_` 前缀是给前端的：InterpretationRow 靠 test_name / drug_name
            # 分派渲染，加前缀它们就不会被误判成检验项行或用药行。
            "ref_title": doc.name,
            "ref_kind": corpus.KIND_LABELS.get(doc.kind, ""),
            "ref_source": doc.source,
            "ref_excerpt": doc.text,
            "ref_id": doc.id,
            "ref_score": round(score, 2),
            "ref_source_type": (
                "open_dataset" if doc.quality_tier == "opencmkg"
                else "curated_kb"),
            "ref_quality_tier": doc.quality_tier,
            "ref_source_version": doc.source_version,
            "ref_license": doc.license,
        })
    return refs


def _build_ai_prompt(question: str, refs: List[Dict[str, Any]]) -> str:
    """组装提示词 —— **RAG 的增强（A）那一步**。

    有检索结果时把它放进上下文，并要求模型**优先依据这些条目**作答、
    不足以回答时明说不确定。没有检索结果时**如实说明**，而不是假装有依据。
    """
    lines = "\n".join(
        f"- ID={r['ref_id']}｜{r['ref_title']}（{r['ref_kind']}）："
        f"{r['ref_excerpt']}"
        for r in refs
    )
    if refs:
        evidence_block = (
            "\n\n【本院知识库中与该问题可能相关的条目】\n"
            f"{lines}\n\n"
            "优先依据上面这些条目；它们也是本次允许使用的全部证据。\n"
        )
    else:
        evidence_block = (
            "\n\n本院知识库没有检索到相关条目。必须输出 insufficient，"
            "claims 为空，不得补写任何医学事实。\n"
        )
    block = (
        evidence_block +
        "你只能使用上面的证据，不得利用模型记忆补充症状、病因、药品、"
        "剂量、检查或治疗信息，不要编造。每条医学事实必须引用实际存在的证据ID；"
        "证据不足时输出 insufficient，不得猜测。\n"
        "只输出以下 JSON：\n"
        "{\"answer_mode\":\"grounded 或 insufficient\","
        "\"title\":\"简短标题\","
        "\"claims\":[{\"text\":\"一条医学事实\","
        "\"evidence_ids\":[\"证据ID\"]}],"
        "\"uncertainty\":\"必要的边界说明\"}\n"
    )
    return (
        f"{_AI_SYSTEM}{block}\n"
        f"患者的咨询内容：{question.strip()}\n\n"
        "请按要求输出 JSON。"
    )


def ai_triage(question: str, refs: Optional[List[Dict[str, Any]]] = None
              ) -> Optional[Dict[str, Any]]:
    """AI 兜底作答。成功返回三块内容，失败返回 None（调用方回退引导）。

    `refs` 是已检索到的知识条目（见 _retrieve_context）。没有证据时直接
    返回 None；模型记忆不属于本系统允许使用的医学来源。
    """
    refs = refs or []
    if not config.LLM_API_KEY or not refs:
        return None
    prompt = _build_ai_prompt(question, refs)
    try:
        raw = _call_llm(prompt)
    except Exception as exc:  # noqa: BLE001 - 网络/鉴权异常统一回退
        print(f"[smart-doctor] AI 兜底调用失败，回退引导：{exc}", flush=True)
        return None

    data = _parse_llm_json(raw)
    if not data:
        return None
    from backend.knowledge.grounding import (render_grounded_text,
                                             validate_grounded_payload)
    errors = validate_grounded_payload(data, refs)
    if errors:
        print("[smart-doctor] 证据校验未通过，回退确定性证据模板："
              + "；".join(errors), flush=True)
        return None
    text = render_grounded_text(data)
    if not text:
        return None
    return {
        "title": str(data.get("title") or "有依据的知识解读").strip(),
        "text": text,
        "advice": "以上内容仅用于帮助理解资料，不能替代医生面诊。",
        "actions": ["携带相关资料咨询医生", "症状持续或加重请及时就医"],
        # 紧急程度只由确定性红旗规则在 _finalize 中提升，模型无权决定。
        "urgent": False,
    }


_AI_EXPLAIN_SYMPTOM = """患者描述了症状，系统已从知识库中给出候选就诊科室。
请只把给出的事实改写成通俗中文。

要求：
1. 不替患者下诊断结论——不说"您患了 XX 病"这类话。
2. 不得补充事实中没有出现的疾病、检查、药物或治疗信息。"""

_AI_EXPLAIN_DISEASE = """患者想了解一种疾病，系统已从知识库中
取出该疾病的相关条目（常见症状、常用检查、常用药物、可能并发症、治疗方式）。
请用**通俗的中文**把这些条目讲给患者听，并给出就诊建议。

要求：
1. 以系统给出的条目为准，**不要补充条目之外的医学断言**，不替患者下诊断。
2. 措辞让非医学背景的人能看懂，避免堆砌术语。"""


def _ai_explain(question: str, *, facts: str, system: str
                ) -> Optional[Dict[str, Any]]:
    """让 AI 基于**数据集给出的事实**生成解读与建议。

    与 `_apply_ai_fallback` 的分工不同：那边是"数据集完全没覆盖"时的兜底；
    这边是"数据集已经给了事实，请 AI 补解读与建议"。

    **事实与建议分开标注**：事实标数据集（interpretation.source），
    本函数产出的建议标「AI 智能导诊」（advice.source）——绝不混同。
    未配置 Key 或调用失败时返回 None，由调用方给确定性文案。
    """
    if not config.LLM_API_KEY:
        return None
    evidence = [{
        "ref_id": "structured:current-facts",
        "ref_title": "本次结构化事实",
        "ref_excerpt": facts,
    }]
    prompt = (
        f"{system}\n\n"
        f"患者的咨询：{question.strip()}\n"
        f"知识库给出的事实（ID=structured:current-facts）：\n{facts}\n\n"
        "每条医学事实必须引用 structured:current-facts。"
        "只输出 JSON："
        "{\"answer_mode\":\"grounded\",\"title\":\"简短标题\","
        "\"claims\":[{\"text\":\"一条事实\","
        "\"evidence_ids\":[\"structured:current-facts\"]}],"
        "\"uncertainty\":\"边界说明\"}"
    )
    try:
        raw = _call_llm(prompt)
    except Exception as exc:      # noqa: BLE001 - 网络/鉴权异常统一回退
        print(f"[smart-doctor] AI 解读调用失败，回退确定性文案：{exc}", flush=True)
        return None
    data = _parse_llm_json(raw)
    if not data:
        return None

    from backend.knowledge.grounding import (render_grounded_text,
                                             validate_grounded_payload)
    errors = validate_grounded_payload(data, evidence)
    if errors:
        print("[smart-doctor] 结构化事实解读未通过证据校验："
              + "；".join(errors), flush=True)
        return None
    text = render_grounded_text(data)
    if not text:
        return None
    return {
        "source": _SOURCE_AI,
        "text": text,
        "actions": ["携带相关资料咨询医生", "具体诊疗请遵医嘱"],
        "urgent": False,
    }


def _apply_ai_fallback(base: Dict[str, Any], question: str) -> bool:
    """填 AI 兜底回答（来源：AI 智能导诊）。成功返回 True，失败返回 False。

    **先检索、再生成**（RAG）：检索到的知识条目既进提示词（让答案有依据，
    而不是让模型凭空编），也回填到 `items`（让患者看见"这条回答参考了什么"）。

    没有 API Key 时**直接短路、不做检索**（评审 A2a）：模型不可用则检索
    结果无人消费，白付一轮 15 万条语料的检索开销（实测秒级），而且界面
    上看不到任何差别。
    """
    if not config.LLM_API_KEY:
        return False
    refs = _retrieve_context(question)
    if not refs:
        return False
    ai = ai_triage(question, refs=refs)
    if ai is None:
        # 模型不可用、输出不合法或证据门禁不通过时，仍可直接展示原始证据；
        # 但不再展示任何未经证据支持的模型文本。
        base["interpretation"] = {
            "source": refs[0].get("ref_source") or _SOURCE_KB,
            "title": "检索到的相关资料",
            "text": "模型解释未通过证据校验，以下仅展示可追溯的原始资料。",
            "items": refs,
        }
        base["advice"] = {
            "source": _SOURCE_KB,
            "text": "现有资料不足以支持进一步判断，请咨询医生。",
            "actions": ["携带相关资料咨询医生"],
            "urgent": False,
        }
        return True
    base["interpretation"] = {
        "source": _SOURCE_AI,
        "title": ai["title"],
        "text": ai["text"],
        "items": refs,
    }
    base["advice"] = {
        "source": _SOURCE_AI,
        "text": ai["advice"],
        "actions": ai["actions"],
        "urgent": ai["urgent"],
    }
    return True


# ── 入口 ──────────────────────────────────────────────────────

def _resolve_with_context(intent: str, entity: Optional[str],
                          context: Optional[Dict[str, Any]]
                          ) -> Tuple[str, Optional[str]]:
    """用上一轮的识别结果补上这一轮缺失的部分（「它」「那个」这类指代）。

    **规则刻意保守**——只在确实缺信息时才补，两种情形：

      1. 本轮**完全没识别出意图**（fallback）→ 整轮沿用上一轮。
         「我的血糖结果正常吗」→「严重吗」：第二句自身没有话题，
         但它显然是接着上一句问的。
      2. 本轮识别出了意图、但**没识别出实体**，且**意图与上一轮相同**
         → 只把实体补上。
         「我的血糖结果正常吗」→「它正常吗」：第二句能判出是在问检验
         （「正常吗」是检验信号），但不知道问的是哪一项。

    **第 2 条的"意图相同"是关键，不是多余的谨慎。** 患者先问血糖、再问
    「医生开的药怎么吃」，意图从 lab 变成 medication——这时**绝不能**把
    「血糖」当药品名带过去：那会去查 `drug_name='血糖'`，一条都查不到，
    患者拿到的是"未找到用药记录"，比不补还糟。
    """
    if not context:
        return intent, entity

    prev_intent = context.get("intent")
    prev_entity = context.get("entity")
    if not prev_intent:
        return intent, entity

    if intent == INTENT_FALLBACK:
        return prev_intent, prev_entity or entity

    if entity is None and prev_entity and intent == prev_intent:
        return intent, prev_entity

    return intent, entity


def _finalize(base: Dict[str, Any], question: str) -> Dict[str, Any]:
    """回答出口的最后一道校验：红旗命中即无条件安全升级。

    与前面所有分支（知识库、数据集、RAG、AI 判定）正交——红旗是
    确定性规则层（backend/redflags.py），不受模型是否可用影响。
    """
    base["evidence"] = _evidence_status(base)
    flags = redflag_check(question)
    advice = base.get("advice")
    if advice and flags:
        advice["urgent"] = True
        first = flags[0]
        note = ("⚠ 您的问题涉及「%s」（%s），属于需要立即处理的警示信号，"
                "请立即前往急诊就医，或拨打急救电话（120）。"
                % (first.label, first.matched))
        if advice.get("text"):
            advice["text"] = note + "\n" + advice["text"]
        else:
            advice["text"] = note
        actions = list(advice.get("actions") or [])
        if "立即前往急诊就医" not in actions:
            actions.insert(0, "立即前往急诊就医")
        advice["actions"] = actions
    return base


def _evidence_status(base: Dict[str, Any]) -> Dict[str, Any]:
    """从最终回答结构确定性推导证据状态，所有回答出口共用。"""
    interpretation = base.get("interpretation") or {}
    advice = base.get("advice") or {}
    items = interpretation.get("items") or []
    refs = [item for item in items
            if isinstance(item, dict) and item.get("ref_id")]
    interpretation_source = str(interpretation.get("source") or "")
    advice_source = str(advice.get("source") or "")
    model_used = _SOURCE_AI in {interpretation_source, advice_source}

    if model_used and refs:
        return {
            "status": "grounded",
            "references": len(refs),
            "model_used": True,
            "message": "模型仅负责改写，展示内容已通过证据 ID 门禁。",
        }
    if refs:
        return {
            "status": "raw_evidence",
            "references": len(refs),
            "model_used": False,
            "message": "模型输出未通过门禁，本轮仅展示可追溯的原始资料。",
        }
    is_curated_source = (
        interpretation_source == _SOURCE_KB
        or "OpenCMKG" in interpretation_source
        or "开源数据" in interpretation_source
    )
    if is_curated_source:
        title = str(interpretation.get("title") or "")
        if "换个问法" in title or "无法" in title:
            return {
                "status": "insufficient",
                "references": 0,
                "model_used": False,
                "message": "现有资料不足，系统没有生成新的医学结论。",
            }
        return {
            "status": "curated",
            "references": 0,
            "model_used": model_used,
            "message": "回答来自医院审核资料或结构化开源数据。",
        }
    return {
        "status": "not_applicable",
        "references": 0,
        "model_used": model_used,
        "message": "本轮没有可展示的医学证据。",
    }


def ask(question: str, subject_id: str,
        context: Optional[Dict[str, Any]] = None, *,
        token: Optional[Token] = None,
        datasource_id: str = config.DEMO_DATASOURCE_ID) -> Dict[str, Any]:
    """处理一次提问，返回三块结构的响应（供路由直接返回）。

    `context` 是上一轮的 `{intent, entity}`，用于解析指代（见
    _resolve_with_context）。不传即等价于单轮问答。
    """
    kb = load_knowledge()
    raw_intent, raw_entity = parse_intent(question)
    intent, entity = _resolve_with_context(raw_intent, raw_entity, context)
    subject = subject_id or ""

    base = {
        "intent": intent,
        "entity": entity,
        "question": question,
        "data": None,
        "interpretation": None,
        "advice": None,
        # 知识问答不访问主库，故默认不适用；只有 lab/medication 数据读取
        # 才由统一审计—执行管线填入报告编号与决策追踪号。
        "audit_status": "not_applicable",
        "report_id": None,
        "trace_id": "",
    }

    # ── 检查报告解读 ──
    if intent == INTENT_LAB:
        # parse_intent 只给**主**检验项；这里取全部命中项，问题里提到几个
        # 就查几个（"我的血脂和血糖结果正常吗"不能只回血糖）。
        tests = _find_entities(question, kb.get("lab_tests") or {})
        if tests:
            entity = tests[0]
        plan = _build_lab_plan(subject, tests)
        personal = fetch_personal_data(
            plan, subject, question=question, token=token,
            datasource_id=datasource_id, persist=token is not None)
        adm, deg, cols, rows, final_sql = personal
        report_id, trace_id = personal.report_id, personal.trace_id
        base["admission"] = adm
        base["degradation"] = deg
        base["audit_status"] = (
            "blocked" if not adm.get("passed") or deg.get("level") == "L3"
            else "passed")
        base["report_id"] = report_id
        base["trace_id"] = trace_id
        if cols is None:
            base["interpretation"], base["advice"] = _unavailable(adm, deg, "检查数据")
            return _finalize(base, question)

        base["data"] = {
            "source": _SOURCE_DATA,
            "columns": cols or [],
            "rows": rows,
            "sql_after": final_sql,
            "degradation_level": deg.get("level", "L0"),
        }

        items = []
        acuity = _DEFAULT_ACUITY
        if entity is None and rows:
            entity = str(rows[0][0])  # 未指定检验项 → 以最近一条为准
        for row in rows:
            name, val, unit, date = (list(row) + [None, None, None, None])[:4]
            hit = interpret_lab(str(name), str(val or ""))
            if hit:
                # 本次回答取所有条目里最高的分诊等级（数字最小者）
                acuity = min(acuity, hit["acuity"])
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
            "text": _text((entry or {}).get("desc")),
            "items": items,
        }
        advice_text = (entry or {}).get("advice_common") or []
        actions = list(advice_text) if isinstance(advice_text, list) else []
        # urgent_text 属 safety-net 提示：无条件展示。是否弹急诊横幅由本次
        # 命中的**档位等级**决定，与这条文案的有无无关（原先正是把它当成了
        # 触发器，于是任何血糖问询——包括正常值和查无记录——都会弹红条）。
        text = "；".join(actions) if actions else ""
        safety = (entry or {}).get("urgent_text") or ""
        if safety:
            text = (text + "。" if text else "") + safety
        base["advice"] = {
            "source": _SOURCE_KB,
            "text": text or "建议将本次结果带给您的主治医生做整体评估。",
            "actions": actions,
            "urgent": acuity <= _ACUITY_URGENT_MAX,
        }
        if not items:
            base["advice"]["text"] = "未在您的记录中找到相关检验，可到查询控制台确认或在下次化验后查看。"
        return _finalize(base, question)

    # ── 用药说明 ──
    if intent == INTENT_MEDICATION:
        plan = _build_medication_plan(subject, entity)
        personal = fetch_personal_data(
            plan, subject, question=question, token=token,
            datasource_id=datasource_id, persist=token is not None)
        adm, deg, cols, rows, final_sql = personal
        report_id, trace_id = personal.report_id, personal.trace_id
        base["admission"] = adm
        base["degradation"] = deg
        base["audit_status"] = (
            "blocked" if not adm.get("passed") or deg.get("level") == "L3"
            else "passed")
        base["report_id"] = report_id
        base["trace_id"] = trace_id
        if cols is None:
            base["interpretation"], base["advice"] = _unavailable(adm, deg, "用药记录")
            return _finalize(base, question)

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
        return _finalize(base, question)

    # ── 症状 → 科室 ──
    if intent == INTENT_SYMPTOM:
        base["admission"] = {"passed": True, "reason": None,
                             "checked_tables": [], "bound_to_subject": False}
        base["degradation"] = {"level": "L0", "label": "通过",
                               "message_cn": "", "message": ""}

        # 科室候选来自**开源数据集**（OpenCMKG 的症状→疾病→科室两跳聚合），
        # 不是自写内容。给候选列表而不是单一科室：实测 top1 只有 65%，
        # 而导诊本来就该给候选。
        triage = dataset_kb.lookup_symptom(entity or question)
        if triage:
            cands = triage["candidates"]
            top = cands[0]["department"] if cands else ""
            base["interpretation"] = {
                "source": dataset_kb.source_note(),
                "title": f"关于「{entity or triage['matched']}」的就诊科室",
                "text": (f"根据开源医学知识图谱中 **{triage['total']} 个相关疾病**的"
                         f"科室分布，以下科室最常见（仅供参考，请以医生判断为准）。"),
                "items": [
                    {"ref_title": c["department"], "ref_kind": "就诊科室",
                     "ref_source": dataset_kb.source_note(),
                     "ref_excerpt": f"数据集中有 {c['disease_count']} 个相关疾病归入该科室"}
                    for c in cands
                ],
            }
            # 解读与建议交给 AI，**来源标注「AI 智能导诊」**——
            # 与上面来自数据集的科室候选分开，绝不混同。
            cands_text = "、".join(
                f"{c['department']}（{c['disease_count']} 个相关疾病）"
                for c in cands[:3])
            advice = _ai_explain(question, facts=f"候选就诊科室：{cands_text}",
                                 system=_AI_EXPLAIN_SYMPTOM)
            if advice:
                base["advice"] = advice
            else:
                base["advice"] = {
                    "source": dataset_kb.source_note(),
                    "text": (f"建议优先咨询{top}。若症状加重或持续不缓解，"
                             "请及时就医；急性剧烈不适应直接急诊。"),
                    "actions": [f"请咨询{top}", "症状加重或持续不缓解请及时就医"],
                    "urgent": False,
                }
            return _finalize(base, question)

        # 数据集里查不到 → AI 兜底（或确定性引导）
        if _apply_ai_fallback(base, question):
            return _finalize(base, question)
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
        return _finalize(base, question)

    # ── 疾病了解 ──
    if intent == INTENT_DISEASE:
        base["admission"] = {"passed": True, "reason": None,
                             "checked_tables": [], "bound_to_subject": False}
        base["degradation"] = {"level": "L0", "label": "通过",
                               "message_cn": "", "message": ""}

        # 事实来自**开源数据集**（OpenCMKG 的疾病关系聚合），不是自写文案。
        info = dataset_kb.lookup_disease(entity or "")
        if info:
            facts = info["facts"]
            dept = facts.get("dept") or "相应科室"
            base["interpretation"] = {
                "source": dataset_kb.source_note(dataset_kb.DISEASE_FILE),
                "title": f"关于「{info['matched']}」",
                "text": ("以下条目来自开源医学知识图谱，**仅供参考**，"
                         "请以医生判断为准。"),
                "items": dataset_kb.disease_fact_lines(facts),
            }
            # 解读与建议交给 AI，来源标注「AI 智能导诊」——与上面的事实分开
            lines = [f"- {label}：{'、'.join(facts[k])}"
                     for k, label in dataset_kb.DISEASE_FIELDS if facts.get(k)]
            advice = _ai_explain(
                question,
                facts=f"疾病：{info['matched']}\n所属科室：{dept}\n"
                      + "\n".join(lines),
                system=_AI_EXPLAIN_DISEASE)
            if advice:
                base["advice"] = advice
            else:
                base["advice"] = {
                    "source": dataset_kb.source_note(dataset_kb.DISEASE_FILE),
                    "text": f"建议就诊科室：{dept}。具体诊疗请遵医嘱。",
                    "actions": [f"可咨询{dept}", "具体诊疗请遵医嘱"],
                    "urgent": False,
                }
            return _finalize(base, question)

        # 数据集里没有这个疾病 → AI 兜底
        if _apply_ai_fallback(base, question):
            return _finalize(base, question)
        base["interpretation"] = {
            "source": _SOURCE_KB,
            "title": "暂未收录该疾病",
            "text": "您可以直接问「我想了解糖尿病」这类常见疾病，或到查询控制台查阅您的检查结果。",
            "items": [],
        }
        base["advice"] = {"source": _SOURCE_KB, "text": "",
                          "actions": [], "urgent": False}
        return _finalize(base, question)

    # ── 兜底（未识别的问法）──
    base["admission"] = {"passed": True, "reason": None,
                         "checked_tables": [], "bound_to_subject": False}
    base["degradation"] = {"level": "L0", "label": "通过",
                           "message_cn": "", "message": ""}
    if _apply_ai_fallback(base, question):
        return _finalize(base, question)
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
    return _finalize(base, question)
