"""红旗规则层：独立于 LLM 与知识图谱的确定性安全升级。

当患者问题命中红旗词（胸痛、呼吸困难、意识障碍、大出血、严重过敏等）时，
**无条件**把 advice.urgent 置为 True 并在建议文本最前加入急诊指引——即使
AI 判定未急症、知识图谱未覆盖、未配置模型，也一样升级。

为什么需要这一层（2026-09 代码评审 P1）：
新版把分诊等级的判断交给了模型，未配置 API Key 或模型调用失败时兜底统一
返回 urgent=False——「我胸痛」这类急症可能不弹急诊横幅。红旗层是回答组装
出口前的**最后一道安全网**，与前面所有分支（知识库、数据集、RAG、AI）正交：
命中即升级，不受它们的判定结果影响。

精度优先的取舍：只收录高特异性的急症表述（并带上常用口语变体），
**不**收录模糊词（如「胸闷」「心疼」「肚子疼」）——医疗导诊里
「人人弹警告」会把真信号稀释掉（与 acuity 分诊的设计一致）。
"""
from dataclasses import dataclass

# (警示标签, 触发词组)。词组按子串匹配；同一组内长词在前（「胸口剧痛」
# 先于「胸口痛」命中），避免显示的是短词。
RED_FLAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("大出血", ("大出血", "血流不止", "大量出血", "出血不止", "止不住血", "呕血", "咯血")),
    ("意识障碍", ("昏迷", "晕厥", "失去意识", "神志不清", "意识不清", "叫不醒", "抽搐", "晕倒")),
    ("呼吸困难", ("呼吸困难", "喘不上气", "喘不过气", "无法呼吸", "窒息")),
    ("剧烈胸痛", ("胸口剧痛", "胸前区疼痛", "胸骨后疼痛", "心绞痛", "胸口痛", "胸口疼", "胸痛")),
    ("严重过敏", ("过敏性休克", "严重过敏", "全身过敏反应")),
    ("心脑血管急症", ("心肌梗死", "心脏骤停", "脑溢血", "脑梗塞", "脑梗", "中风")),
)

_URGENT_NOTE = (
    "您的问题涉及「{label}」（{matched}），属于需要立即处理的警示信号，"
    "请立即前往急诊就医，或拨打急救电话（120）。"
)


@dataclass(frozen=True)
class RedFlag:
    """一次红旗命中的描述。"""

    label: str
    matched: str


def check(question: str) -> tuple[RedFlag, ...]:
    """返回命中的红旗（可能多个）。零 IO、零网络、不依赖模型与知识图谱。"""
    if not question:
        return ()
    hits = []
    for label, phrases in RED_FLAGS:
        for phrase in phrases:
            if phrase in question:
                hits.append(RedFlag(label=label, matched=phrase))
                break  # 每组只记一次命中
    return tuple(hits)


def urgent_note(flag: RedFlag) -> str:
    """给界面使用的急诊指引文案。"""
    return _URGENT_NOTE.format(label=flag.label, matched=flag.matched)