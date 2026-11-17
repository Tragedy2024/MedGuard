"""带来源的通用疾病分类资料。只回答枚举，不推断患者分型。

正文为项目编写的中文事实摘要，链接保留机构原题名。与OpenCMKG关系卡
分开维护，避免把有两个子型条目误当成完整医学分类。无需模型或患者数据。
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Dict, Optional

import yaml


@lru_cache(maxsize=1)
def _entries():
    try:
        data = yaml.safe_load(Path(__file__).with_suffix('.yaml').read_text(encoding='utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        return ()
    if not isinstance(data, dict) or data.get('version') != 1:
        return ()
    return tuple(e for e in data.get('entries', [])
                 if isinstance(e, dict) and e.get('entity') and e.get('items')
                 and str(e.get('url', '')).startswith('https://'))


def _question_text(question: str) -> str:
    q = re.sub(r'\s+', '', question).strip('。？！!?，,')
    return re.sub(r'^(?:(?:请问|请介绍一下|介绍一下|请告诉我|我想知道|我想了解一下|我想了解))+', '', q)


def lookup(question: str, context: Optional[Dict[str, Any]] = None):
    """有边界的枚举语法，全文匹配防止截走药品、症状、个人诊断或复合问题。"""
    q = _question_text(question)
    if context and context.get('intent') == 'disease' and re.fullmatch(
            r'(?:那|它|这个病|这种病)?(?:有几种|分几种|分几类|分几型|有哪些类型)(?:呢|吗)?', q):
        q = str(context.get('entity') or '') + re.sub(r'^(?:那|它|这个病|这种病)', '', q)
    enumeration = r'(?:有(?:哪几种|几种|哪些(?:类型|种类|分型)?|什么(?:类型|种类))|分(?:为)?(?:哪几种|几种|几类|几型)|的?(?:分型|分类|类型|种类))'
    for entry in _entries():
        for alias in entry.get('aliases') or [entry['entity']]:
            subject = r'(?:常见的?)?' + re.escape(alias)
            forward = subject + r'(?:通常|主要|一般|一共)?(?:都)?' + enumeration + r'(?:呢|吗|有哪些|是什么)?'
            reverse = r'(?:都)?(?:有|包括)?(?:哪几种|哪些(?:类型的)?|几种)(?:常见的?)?' + re.escape(alias)
            if re.fullmatch(forward + '|' + reverse, q):
                return deepcopy(entry)
    return None


def interpretation(entry):
    items = []
    for item in entry['items']:
        items.append({
            'ref_title': item['title'], 'ref_kind': '分类资料',
            'ref_excerpt': item['text'], 'ref_source': entry['source'],
            'ref_source_type': 'public_health', 'ref_quality_tier': 'public_reference',
            'ref_url': item.get('url', entry['url']),
            'ref_original_title': item.get('original_title', entry['original_title']),
            'ref_checked_on': entry['checked_on'],
        })
    return {'source': entry['source'], 'title': entry['entity'] + '的分类',
            'text': entry['summary'], 'items': items}
