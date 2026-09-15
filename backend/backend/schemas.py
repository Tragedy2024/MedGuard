"""前后端契约（Pydantic 模型）。

字段名即契约。改字段名 = 契约变更，必须走团队规范 §3.4 流程
（此处即后端侧定义，前端由 openapi-typescript 从 openapi.json 生成）。
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Token(BaseModel):
    type: Literal["staff", "patient"]
    subject_id: Optional[str] = None   # 病患令牌必填，医护令牌为 None


class QueryRequest(BaseModel):
    token: Token
    datasource_id: str
    question_id: str


class AdmissionInfo(BaseModel):
    passed: bool
    reason: Optional[str] = None
    checked_tables: List[str] = Field(default_factory=list)
    bound_to_subject: bool = False


class PlanItem(BaseModel):
    id: int
    description: str
    sql_before: str
    sql_after: str
    is_final: bool


class SecurityEvent(BaseModel):
    sub_query_id: str
    type: str
    type_label: str
    column: str
    severity: str
    severity_label: str
    detail: str


class RewriteInfo(BaseModel):
    applied: int = 0
    log: List[str] = Field(default_factory=list)


class DegradationInfo(BaseModel):
    level: str
    label: str
    """算法层原文（英文）。保留供技术观众核对，界面折叠展示。"""
    message: str = ""
    """产品层中文说明。面向医护与病患的主文案——算法层英文原文不该
    直接出现在界面上（会议记录 §1.1 已否决的开发者工具形态）。"""
    message_cn: str = ""


class MetricsInfo(BaseModel):
    """审计阶段的开销——**不是查询执行的开销**。

    `llm_calls` / `db_access` 恒为 0，这是「零 LLM、零查库」安全声明的
    可验证形式：审计器不调模型（无提示注入面）、不碰数据库（无困惑代理面）。
    查询本身的执行不计入本指标——`result` 非空即表示执行过。
    """
    elapsed_ms: int = 0      # 层一 + 层二的审计耗时
    llm_calls: int = 0       # 恒为 0
    db_access: int = 0       # 恒为 0


class ResultSet(BaseModel):
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)


class QueryResponse(BaseModel):
    admission: AdmissionInfo
    question: str
    plan: List[PlanItem] = Field(default_factory=list)
    events: List[SecurityEvent] = Field(default_factory=list)
    rewrite: RewriteInfo = Field(default_factory=RewriteInfo)
    degradation: DegradationInfo
    metrics: MetricsInfo = Field(default_factory=MetricsInfo)
    result: Optional[ResultSet] = None


class DatasourceInfo(BaseModel):
    id: str
    name: str
    table_count: int
    column_count: int
    policy_ready: bool


class ReportSummary(BaseModel):
    id: int
    question: str
    token_type: str
    created_at: str
    degradation_level: str
    event_count: int


class ReportDetail(ReportSummary):
    """报告详情 = 摘要字段 + 完整 QueryResponse 快照。"""
    payload: Dict[str, Any] = Field(default_factory=dict)


class SchemaColumn(BaseModel):
    name: str
    type: str
    pk: bool = False


class SchemaTable(BaseModel):
    name: str
    columns: List[SchemaColumn] = Field(default_factory=list)


class DatasourceSchema(BaseModel):
    tables: List[SchemaTable] = Field(default_factory=list)


class DemoDatasourceResult(BaseModel):
    id: str
    created: bool


class CrossDomainRule(BaseModel):
    """跨域关联规则。

    字段给默认值是刻意的：策略 YAML 由人手工维护，缺字段时应能在界面上
    显示为一条"不完整规则"，而不是让整个策略页 500。结构合法性由算法层
    `load_ssa` 把关（`/api/policies/{id}/validate` 暴露其结果）。
    """
    table_pair: List[str] = Field(default_factory=list)
    join_key: str = ""
    forbid_personal_level: bool = False
    allow_aggregate_level: bool = False
    reason: str = ""


class Policy(BaseModel):
    datasource_id: str
    column_labels: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    column_reasons: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    cross_domain_rules: List[CrossDomainRule] = Field(default_factory=list)
    review_status: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    """面向医护/病患的中文名。物理表名与列名不应直接出现在界面上——
    那会让产品退回成开发者工具（会议记录 §1.1 已否决的形态）。
    缺失时前端回落到物理名，但 tests 会拦住缺失。"""
    table_aliases: Dict[str, str] = Field(default_factory=dict)
    column_aliases: Dict[str, Dict[str, str]] = Field(default_factory=dict)


class PolicyList(BaseModel):
    datasource_ids: List[str] = Field(default_factory=list)


class PolicyUpdateResult(BaseModel):
    saved: bool
    datasource_id: str


class PolicyValidation(BaseModel):
    """策略可加载性校验。

    成功与失败两种形状：成功时带 tables/columns/cross_domain_rules 计数，
    失败时带 error 文案，故后四者均可为空。
    """
    ok: bool
    datasource_id: str
    tables: Optional[int] = None
    columns: Optional[int] = None
    cross_domain_rules: Optional[int] = None
    error: Optional[str] = None


class MetricValue(BaseModel):
    value: float
    detail: str


class DetectionMetrics(BaseModel):
    """检测效能——数字来自论文仓库 results/rq3/ 实测，不得杜撰。"""
    precision: MetricValue
    recall: MetricValue
    blocked_detection: MetricValue
    source: str


class HealthInfo(BaseModel):
    status: str
    algo_engine: str
    policies: List[str] = Field(default_factory=list)
    routes: List[str] = Field(default_factory=list)