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

# 面向用户的中文说明。算法层返回的 degradation_message 是英文原文
# （如 "Cannot compute exact result due to privacy policy constraints..."），
# 直接展示给医护与病患是不合适的——本产品的用户不是数据库专业人员。
# 算法层一行不改（红线），故在**产品层**并置一条中文说明，原文保留供
# 技术观众在「技术详情」里核对。
DEGRADATION_MESSAGES = {
    "L0": "",
    "L1": "为确保不泄露个人信息，部分明细已替换为聚合结果。",
    "L2": "为确保不泄露个人信息，本次查询无法给出完全精确的结果；"
          "以下是与您的问题相关的另一种统计口径。",
    "L3": "该查询涉及其他患者信息，无法提供。",
}

# 计划里有无法解析的子查询时用这条。
#
# **不能复用 L3 的默认文案**：那条说的是"涉及其他患者信息"，而解析失败
# 与患者隐私毫无关系。混用会让用户以为自己触发了隐私规则，从而去换问法
# 试探——完全找错方向。实际原因是生成的查询语句本身有问题。
PARSE_FAILED_MESSAGE = (
    "这条问法暂时无法处理：系统生成的查询语句没能通过解析。"
    "请换一种说法，或从常用问题中选择。"
)

TOKEN_LABELS = {
    "staff": "医护人员",
    "patient": "病患",
}