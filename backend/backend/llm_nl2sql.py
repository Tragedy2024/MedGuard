"""自然语言 → 多步分解计划（MAC-SQL 体系）。

**这是翻译环节，不是审计环节。** 医盾审计仍然零 LLM、零查库——本模块
只是把一句中文问话翻译成一份查询计划，计划随后照常走层一准入与层二审计。
「零 LLM」的安全声明说的是审计器不调模型，与翻译无关（设计文档 §1.2）。

用 MAC-SQL 的 `Decomposer`，且必须 `dataset_name='bird'`：那个分支的模板
（`decompose_template_bird`）输出「Sub question N + ```sql 块」格式，正是
vendor 里已有的 `decomposer_parser.parse_qa_pairs` 能解析的形状。
spider 分支输出单条 SQL，没有中间步骤，安全演示会失去「中间结果暴露」——
本产品不使用。
"""
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, Tuple

from backend import config


class NL2SQLError(RuntimeError):
    """翻译失败：无 Key、网络不可达、或模型输出无法解析成计划。"""


def llm_available() -> bool:
    """是否具备调用模型的条件。无 Key 时调用方应回落或给出明确提示。"""
    return bool(config.LLM_API_KEY)


def _algo_path() -> str:
    src = config.ALGO_SRC_DIR
    if not src or not os.path.isdir(src):
        raise NL2SQLError("未找到内置算法层（vendor/nl2sql/src）。")
    return src


def _load_schema_meta(datasource_id: str) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
    """取表中文名与列中文名。取不到就退回物理名——不能让别名缺失挡住翻译。"""
    import yaml
    path = os.path.join(config.SSA_DIR, f"{datasource_id}.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        return {}, {}
    return data.get("table_aliases") or {}, data.get("column_aliases") or {}


def _value_examples(con: sqlite3.Connection, table: str, col: str) -> List[str]:
    """给文本列取几个真实取值。

    MAC-SQL 的模板靠 Value examples 让模型把中文词对上列名（如
    「糖尿病」→ diagnosis_name）。只取低基数文本列，避免把数字列刷屏。
    """
    try:
        rows = con.execute(
            f'SELECT DISTINCT "{col}" FROM "{table}" '
            f'WHERE "{col}" IS NOT NULL LIMIT 4'
        ).fetchall()
    except sqlite3.Error:
        return []
    vals = [str(r[0]) for r in rows if str(r[0]).strip()]
    if not vals or len(vals) > 4:
        return []
    # 全是长数字/ID 的列没有示例价值
    if all(v.isdigit() and len(v) > 4 for v in vals):
        return []
    return vals


def build_schema_text(datasource_id: str) -> Tuple[str, str]:
    """生成 MAC-SQL 模板要的【Database schema】与【Foreign keys】两段。

    比 BIRD 原件多做一件事：把中文业务名一并写进去。本产品的提问是中文，
    而列名是英文（diagnosis_name 之类），不给中文对照，模型很难对上
    「糖尿病」「费用」这些词。
    """
    db = config.BUSINESS_DB
    if not os.path.exists(db):
        raise NL2SQLError("演示库尚未载入，无法生成表结构描述。")

    table_aliases, column_aliases = _load_schema_meta(datasource_id)

    con = sqlite3.connect(db)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]

        lines: List[str] = []
        pk_of: Dict[str, str] = {}
        cols_of: Dict[str, List[str]] = {}

        for t in tables:
            info = list(con.execute(f'PRAGMA table_info("{t}")'))
            cols = [r[1] for r in info]
            cols_of[t] = cols
            pk = next((r[1] for r in info if r[5]), None)
            if pk:
                pk_of[t] = pk

            cn = table_aliases.get(t)
            lines.append(f"# Table: {t}" + (f" ({cn})" if cn else ""))
            lines.append("[")
            for r in info:
                name, ctype = r[1], (r[2] or "").upper()
                ca = column_aliases.get(t, {}).get(name)
                # 有中文名就用中文——提问是中文，模型靠它把词对上列。
                # 没别名才回落到列名，避免出现「bill_id. bill_id.」这种重复。
                desc = f"{ca}. " if ca else f"{name}. "
                ex = _value_examples(con, t, name)
                if ex:
                    desc += f"Value examples: {ex}. "
                lines.append(f"  ({name}, {ctype}. {desc}),")
            lines.append("]")

        # 外键：库中未声明，按「同名列指向别表主键」推断
        fks: List[str] = []
        for t, cols in cols_of.items():
            for c in cols:
                if c == pk_of.get(t):
                    continue
                for t2, pk in pk_of.items():
                    if t2 != t and pk == c:
                        fks.append(f"{t}.{c} = {t2}.{pk}")
                        break

        fk_str = "\n".join(fks) if fks else "（无显式外键；同名列可作连接线索）"
        return "\n".join(lines), fk_str
    finally:
        con.close()


def _normalize_output(raw: str) -> str:
    """把模型输出规整成 `decomposer_parser` 认的格式。

    `SUBQ_PATTERN` 要求 `SQL` **独占一行**、下一行才是 ``` 围栏：

        Sub question 1: ……
        SQL
        ```sql
        SELECT …
        ```

    但实测模型的书写习惯不一致。deepseek-v4-flash 会写成
    `SQL: ```sql`（冒号 + 围栏挤在同一行），于是整条解析失败、用户拿到
    "无法处理"——而这只差一次字符串规整。不规整的话约一半提问会失败。

    放在这里而不是改 `decomposer_parser.py`：那是 vendor 代码，保持原样；
    适配属于产品层的责任（与 deps.py 对算法层的做法一致）。
    """
    t = raw or ""
    # 1) SQL 与围栏同行（可能带半角/全角冒号，也可能没有分隔）
    t = re.sub(r'(?im)^[ \t]*SQL[ \t]*[:：]?[ \t]*```', 'SQL\n```', t)
    # 2) 子问题行写成 "SQL:" 单独一行，其后才是围栏——合并掉多余的冒号行
    t = re.sub(r'(?im)^[ \t]*SQL[ \t]*[:：][ \t]*$', 'SQL', t)
    return t


def _binding_hint(token_type: str, subject_id: str) -> str:
    """令牌主体的身份约束，塞进 MAC-SQL 模板的 evidence 槽位。

    **不给这段，模型会写出 `patient_id = :patient_id` 这类参数占位符**——
    它既执行不了（sqlite3 会当成未绑定参数），更要命的是**等于没有绑定到本人**，
    绕过了层一准入的前提。所以这是安全约束，不是提示词优化。

    医护令牌同理（实测）：问「我治疗了哪些患者」而系统不说「我」是谁时，
    模型只能写 `:doctor_id`，那 SQL 解析不了 → 整条查询处理失败。
    给工号之后这类第一人称问法才答得出来。
    """
    if token_type == "patient" and subject_id:
        return (
            f"当前查询者是病患令牌，只能访问患者 {subject_id} 本人的数据。"
            f"凡涉及患者个人的过滤条件，必须直接使用字面量 '{subject_id}'"
            f"（例如 patient_id = '{subject_id}'），"
            f"不要使用 :param 之类的参数占位符，也不要用其他患者编号。"
        )
    if token_type == "staff" and subject_id:
        return (
            f"当前查询者是医护人员令牌，医生工号为 {subject_id}。"
            f"凡涉及本人（「我」「我的患者」「我治疗/接诊」）的条件，"
            f"必须直接使用字面量 '{subject_id}' 过滤，"
            f"例如 SELECT COUNT(DISTINCT patient_id) FROM visits "
            f"WHERE doctor_id = '{subject_id}'。"
            f"不要使用 :param 之类的参数占位符。"
        )
    return ""


def decompose(
    question: str,
    datasource_id: str,
    token_type: str = "staff",
    subject_id: str = "",
) -> List[Dict[str, Any]]:
    """自然语言 → 多步计划 `[{id, description, sql}, ...]`。

    失败一律抛 `NL2SQLError`，附带可读原因——调用方据此决定回落或报错，
    不要在失败时返回空计划（空计划会被当成"无需审计"而静默放行）。
    """
    if not question or not question.strip():
        raise NL2SQLError("问题为空。")
    if not llm_available():
        raise NL2SQLError("未配置模型（缺少 OPENAI_API_KEY），无法翻译新的问法。")

    # 延迟导入：core.api_config 在**导入时**就构造 OpenAI 客户端，没有 Key
    # 会直接抛异常。所以必须放到函数内，让无 Key 的环境也能正常加载本模块。
    src = _algo_path()
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from core.agents import Decomposer
        from core.const import DECOMPOSER_NAME
        from decomposer_parser import parse_qa_pairs
    except Exception as exc:  # noqa: BLE001 - 缺依赖时给出可读原因
        raise NL2SQLError(f"算法层导入失败：{exc}") from exc

    desc_str, fk_str = build_schema_text(datasource_id)

    msg = {
        "idx": 0,
        "db_id": datasource_id,
        "query": question.strip(),
        "evidence": _binding_hint(token_type, subject_id),
        "desc_str": desc_str,
        "fk_str": fk_str,
        "difficulty": "",
        "ground_truth": "",
        "send_to": DECOMPOSER_NAME,
    }

    try:
        Decomposer(dataset_name="bird").talk(msg)
    except Exception as exc:  # noqa: BLE001 - 网络/鉴权/模型异常统一转义
        raise NL2SQLError(f"模型调用失败：{exc}") from exc

    raw = msg.get("qa_pairs") or ""
    tasks = parse_qa_pairs(raw)
    if not tasks:
        # 先规整再试一次——模型书写习惯的差异不该让整条查询失败
        tasks = parse_qa_pairs(_normalize_output(raw))
    if not tasks:
        # 把原文片段带出来——否则"没解析出子问题"这句话无法排查。
        # 实测模型偶尔不按格式作答，没有原文就只能猜。
        snippet = " ".join(raw.split())[:240]
        raise NL2SQLError(
            f"模型输出里没有解析出子问题（原文 {len(raw)} 字符）"
            f"{'，开头是：' + snippet if snippet else '，且输出为空'}。"
        )

    return [
        {"id": t.id, "description": t.description, "sql": t.sql}
        for t in tasks
    ]
