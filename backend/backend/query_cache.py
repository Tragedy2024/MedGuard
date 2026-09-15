"""问答缓存——「问法 → 查询计划」。

**缓存的是计划，不是答案。** 命中缓存后，计划照样走层一准入与层二审计，
安全演示一点不打折。这一点是这个设计成立的前提：如果缓存的是答案，
演示的安全过程就没了。

为什么要缓存（两个理由，第二个更重要）：

1. **速度**：模型翻译一次约 47 秒，演示时不可接受。
2. **质量**：模型倾向选更安全的写法（子查询而非 JOIN），于是审计出来的
   违规数是 0——**演示里没东西可看**。打磨过的计划才会触发 Rule C 拆分
   与「跨域个人级 JOIN」等事件。所以预热缓存不只是提速，是保演示质量。

演示前"热身"的方式就是提前跑几次查询：命中即更新计数，未命中则调模型
生成后写回。
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from backend import config

_LOCK = threading.Lock()


def normalize(text: str) -> str:
    """归一化问法，让「糖尿病患者产生了多少费用 」与「糖尿病患者产生了多少费用」
    命中同一条。

    只做保守处理：去首尾空白、压内部连续空白、去末尾问号、统一全角空格。
    不做同义词展开或模糊匹配——那会让"命中"变得不可预期，演示时反而危险。
    """
    t = (text or "").replace("　", " ").strip()
    t = " ".join(t.split())
    return t.rstrip("？?")


def _empty() -> Dict[str, Any]:
    return {"version": 1, "entries": {}}


def _read() -> Dict[str, Any]:
    path = config.QUERY_CACHE_FILE
    if not os.path.exists(path):
        return _empty()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "entries" not in data:
            return _empty()
        return data
    except (OSError, json.JSONDecodeError):
        # 缓存损坏不该让查询失败——当作空缓存，由调用方回落到模型
        return _empty()


def _write(data: Dict[str, Any]) -> None:
    path = config.QUERY_CACHE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # 原子替换，避免演示中途写坏文件


def _evict(entries: Dict[str, Any], token_type: str, limit: int) -> None:
    """每类令牌只保留最近 N 条。演示缓存要小而可控，不能无限膨胀。"""
    same = [(k, v) for k, v in entries.items()
            if token_type in (v.get("token_types") or [])]
    if len(same) <= limit:
        return
    same.sort(key=lambda kv: kv[1].get("last_used") or "", reverse=True)
    for k, _ in same[limit:]:
        entries.pop(k, None)


def lookup(question: str, token_type: str) -> Optional[List[Dict[str, Any]]]:
    """查缓存。命中返回计划，未命中返回 None。"""
    key = normalize(question)
    if not key:
        return None

    with _LOCK:
        data = _read()
        entry = data["entries"].get(key)
        if not entry:
            return None

        # 问法可能只对某类令牌成立（如病患专属问法），令牌不符视为未命中
        types = entry.get("token_types") or []
        if types and token_type not in types:
            return None

        entry["hits"] = int(entry.get("hits", 0)) + 1
        entry["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _write(data)

    return [dict(s) for s in entry.get("plan") or []]


def store(question: str, plan: List[Dict[str, Any]], token_type: str,
          source: str = "llm") -> None:
    """写入/更新一条缓存。source 记录来源（llm / manual），便于回溯。"""
    key = normalize(question)
    if not key or not plan:
        return

    with _LOCK:
        data = _read()
        entries = data["entries"]
        prev = entries.get(key) or {}
        types = set(prev.get("token_types") or [])
        types.add(token_type)
        entries[key] = {
            "plan": [dict(s) for s in plan],
            "token_types": sorted(types),
            "hits": int(prev.get("hits", 0)),
            "created_at": prev.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_used": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": prev.get("source") or source,
        }
        _evict(entries, token_type, config.QUERY_CACHE_PER_TOKEN)
        _write(data)


def stats() -> Dict[str, Any]:
    """给管理员看缓存现状：多少条、命中分布。"""
    data = _read()
    entries = data["entries"]
    return {
        "count": len(entries),
        "entries": [
            {
                "question": k,
                "steps": len(v.get("plan") or []),
                "token_types": v.get("token_types") or [],
                "hits": int(v.get("hits", 0)),
                "source": v.get("source") or "",
                "last_used": v.get("last_used") or "",
            }
            for k, v in sorted(
                entries.items(),
                key=lambda kv: kv[1].get("last_used") or "",
                reverse=True,
            )
        ],
    }
