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
    message: str = ""


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