"""项目自有的大模型调用适配器。

本模块只封装 OpenAI 兼容接口，不包含 MAC-SQL 源码。调用模型用于自然语言
翻译和知识回答；安全审计仍由静态规则执行，不调用模型或业务数据库。
"""

from __future__ import annotations

from typing import Optional

from backend import config


class LLMCallError(RuntimeError):
    """模型服务不可用、鉴权失败或返回内容为空。"""


def call_llm(prompt: str, *, system: Optional[str] = None,
             timeout: float = 30.0) -> str:
    if not config.LLM_API_KEY:
        raise LLMCallError("未配置模型 API Key。")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMCallError("缺少 openai 依赖。") from exc

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        client = OpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_API_BASE,
            timeout=timeout,
            max_retries=1,
        )
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001 - 统一转换为产品可读错误
        raise LLMCallError(f"模型调用失败：{exc}") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        raise LLMCallError("模型没有返回可用内容。")
    return content.strip()
