"""前后端契约（Pydantic 模型）。

字段名即契约。改字段名 = 契约变更，必须走团队规范 §3.4 流程
（此处即后端侧定义，前端由 openapi-typescript 从 openapi.json 生成）。
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Token(BaseModel):
    """数据访问凭证。

    `exp` / `sig` 由 `POST /api/auth/login` 签发，签名覆盖
    **type / subject_id / account / exp 四者**（少签一个字段，那个字段就能
    被随意改动）。**每个收令牌的端点都会验签**——在此之前令牌是客户端自己
    拼的，手搓一个 `{"type":"patient","subject_id":"P002"}` 就能读别人的
    安全报告。

    `account` 是报告隔离的依据：按 (type, subject_id) 隔离时，管理员
    （subject_id 为空）会和"绑定主体留空"的 staff 撞进同一个桶。
    """
    type: Literal["staff", "patient"]
    subject_id: Optional[str] = None   # 病患令牌必填，医护令牌为 None
    account: Optional[str] = None      # 登录账号（管理员判定要用它回查）
    exp: Optional[int] = None          # 到期时间戳（Unix 秒）
    sig: Optional[str] = None          # HMAC-SHA256 十六进制


class LoginRequest(BaseModel):
    account: str
    password: str


class UserInfo(BaseModel):
    """账号的公开信息——**不含口令哈希**。"""
    account: str
    role: Literal["admin", "staff", "patient"]
    subject_id: Optional[str] = None
    display_name: str = ""


class LoginResponse(BaseModel):
    user: UserInfo
    token: Token


class PolicyUpdateRequest(BaseModel):
    """更新安全策略。**只有管理员能改**——这两个接口决定了医盾拦什么。"""
    token: Token
    column_labels: Optional[Dict[str, Dict[str, str]]] = None
    cross_domain_rules: Optional[List[Dict[str, Any]]] = None


class DemoLoadRequest(BaseModel):
    """一键载入演示数据。**只有管理员能调**——它会删掉并重建业务库。"""
    token: Token


class RegisterRequest(BaseModel):
    """建号。**只有管理员令牌能调**（医院里开号是信息科的活）。"""
    token: Token
    account: str
    password: str
    role: Literal["admin", "staff", "patient"]
    subject_id: Optional[str] = None   # 病患必填（绑定本人），管理员必须为空
    display_name: str = ""


class QueryRequest(BaseModel):
    token: Token
    datasource_id: str
    question_id: str


class AdmissionInfo(BaseModel):
    passed: bool
    reason: Optional[str] = None
    checked_tables: List[str]
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
    applied: int
    log: List[str]


class DegradationInfo(BaseModel):
    # 用 Literal 而不是 str：生成给前端的 TS 类型才能精确到联合类型，
    # 而且 Pydantic 会真的拒掉非法等级——比让它在界面上以字符串乱窜好。
    level: Literal["L0", "L1", "L2", "L3"]
    label: str
    """算法层原文（英文）。保留供技术观众核对，界面折叠展示。"""
    message: str
    """产品层中文说明。面向医护与病患的主文案——算法层英文原文不该
    直接出现在界面上（会议记录 §1.1 已否决的开发者工具形态）。"""
    message_cn: str = ""


class MetricsInfo(BaseModel):
    """审计阶段的开销——**不是查询执行的开销**。

    `llm_calls` / `db_access` 恒为 0，这是「零 LLM、零查库」安全声明的
    可验证形式：审计器不调模型（无提示注入面）、不碰数据库（无困惑代理面）。
    查询本身的执行不计入本指标——`result` 非空即表示执行过。
    """
    elapsed_ms: int          # 层一 + 层二的审计耗时
    llm_calls: int           # 恒为 0
    db_access: int           # 恒为 0


class ResultSet(BaseModel):
    columns: List[str]
    rows: List[List[Any]]


class QueryResponse(BaseModel):
    admission: AdmissionInfo
    question: str
    plan: List[PlanItem]
    events: List[SecurityEvent]
    rewrite: RewriteInfo
    degradation: DegradationInfo
    metrics: MetricsInfo
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
    token_type: Literal["staff", "patient"]
    created_at: str
    degradation_level: Literal["L0", "L1", "L2", "L3"]
    event_count: int


class ReportDetail(ReportSummary):
    """报告详情 = 摘要字段 + 完整 QueryResponse 快照。

    `payload` 声明为 `QueryResponse` 而不是 `Dict[str, Any]`：它本来就是
    那次查询的完整响应（存库时就是 `resp.model_dump()`）。写成泛型字典会让
    安全报告页在 TS 里整页退化成 unknown——而那正是承载审计证据的核心页面。
    """
    payload: QueryResponse


class SchemaColumn(BaseModel):
    name: str
    type: str
    pk: bool = False


class SchemaTable(BaseModel):
    name: str
    columns: List[SchemaColumn]


class DatasourceSchema(BaseModel):
    tables: List[SchemaTable]


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
    # ECL 三值是**封闭集合**，用 Literal 让生成类型精确到联合类型，
    # 同时 Pydantic 会拒掉非法标签（策略写坏时 fail-closed，而不是
    # 让一个不认识的等级悄悄进入审计）。
    column_labels: Dict[str, Dict[str, Literal["free", "controlled", "blocked"]]]
    column_reasons: Dict[str, Dict[str, str]]
    cross_domain_rules: List[CrossDomainRule]
    review_status: Dict[str, Dict[str, str]]
    """面向医护/病患的中文名。物理表名与列名不应直接出现在界面上——
    那会让产品退回成开发者工具（会议记录 §1.1 已否决的形态）。
    缺失时前端回落到物理名，但 tests 会拦住缺失。"""
    table_aliases: Dict[str, str]
    column_aliases: Dict[str, Dict[str, str]]


class PolicyList(BaseModel):
    datasource_ids: List[str]


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
    policies: List[str]
    routes: List[str]

# ════════════════════════════════════════════════════════════════
# 智慧医生（面向患者的可信就医助手，见 docs/智慧医生.docx）
# ════════════════════════════════════════════════════════════════

class SmartDoctorRequest(BaseModel):
    token: Token
    datasource_id: str
    question: str


class SmartDoctorDataBlock(BaseModel):
    """院内数据（来源：医院主库）。经医盾层一准入 + 层二审计后返回。"""
    source: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    sql_after: str = ""
    degradation_level: str = "L0"


class SmartDoctorInterpretation(BaseModel):
    """通俗解读（来源：医院审核知识库）。

    items 为逐条说明（如每个检验项/每种药对应一条），字段随意图不同，
    故用宽松字典——测试会逐键断言，前端按意图渲染。
    """
    source: str
    title: str = ""
    text: str = ""
    items: List[Dict[str, Any]] = Field(default_factory=list)


class SmartDoctorAdvice(BaseModel):
    """下一步建议（来源：医院审核知识库）。"""
    source: str
    text: str = ""
    actions: List[str] = Field(default_factory=list)
    urgent: bool = False


class SmartDoctorResponse(BaseModel):
    intent: Literal["lab", "medication", "symptom", "disease", "fallback"]
    question: str
    admission: AdmissionInfo
    degradation: DegradationInfo
    data: Optional[SmartDoctorDataBlock] = None
    interpretation: Optional[SmartDoctorInterpretation] = None
    advice: Optional[SmartDoctorAdvice] = None
