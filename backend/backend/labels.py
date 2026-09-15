"""中文映射。所有面向用户的算法层术语在此集中定义。

后续要改界面措辞，只改这个文件，不动路由。
"""

VIOLATION_LABELS = {
    "column_unnecessary_exposure": "不必要的列暴露",
    "column_needs_aggregation": "需聚合化",
    "blocked_column_in_select": "禁止列出现在 SELECT",
    "cross_domain_personal_join": "跨域个人级 JOIN",
    "blocked_column_in_derived": "派生表达式引用禁止列",
    "controlled_column_in_derived": "派生表达式暴露受控列",
}

SEVERITY_LABELS = {
    "rewritable": "可拦截",
    "degradable": "需降级",
    "must_degrade": "必须阻断",
}

DEGRADATION_LABELS = {
    "L0": "通过",
    "L1": "聚合替代",
    "L2": "意图变更",
    "L3": "已拒绝",
}

TOKEN_LABELS = {
    "staff": "医护人员",
    "patient": "病患",
}