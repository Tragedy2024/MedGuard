"""公开疾病目录：区分名称覆盖与简介覆盖，支持有限页面而非固定回答条数。"""
import re

from backend import dataset_kb
from backend.knowledge import corpus, public_guidance

PAGE_SIZE = 12


def page(kb, *, offset=0, requested_count=None):
    entries = {}
    for doc in corpus.build_docs(kb):
        overview = dict(doc.sections).get('overview', '').strip()
        if doc.kind == 'disease' and overview:
            entries[doc.name] = {
                'ref_title': doc.name, 'ref_kind': '疾病简介',
                'ref_source': doc.source or '项目整理知识库',
                'ref_excerpt': re.split(r'[，。；]', overview, maxsplit=1)[0],
            }
    # 公开来源的概述也属于已可展示的内容，不把关系图谱的空字段算作简介。
    for topic in public_guidance._catalog().get('topics', {}).values():
        entry = topic.get('fields', {}).get('overview')
        if topic.get('intent') != 'disease' or not entry or not entry.get('items'):
            continue
        source = entry['items'][0]
        entries[topic['entity']] = {
            'ref_title': topic['entity'], 'ref_kind': '疾病简介',
            'ref_excerpt': entry['summary'], 'ref_source': source['source'],
            'ref_url': source['url'], 'ref_original_title': source['original_title'],
            'ref_source_type': 'public_health',
        }
    rows = list(entries.values())
    names = {corpus.normalize_entity(name) for name in dataset_kb.disease_names() if name}
    names.update(corpus.normalize_entity(name) for name in entries)
    total = len(rows)
    limit = min(requested_count, total) if requested_count is not None else total
    offset = max(0, offset)
    selected = rows[offset:min(offset + PAGE_SIZE, limit)]
    end = offset + len(selected)
    more = end < limit and bool(selected)
    return selected, {
        'total_diseases': len(names), 'overview_count': total,
        'offset': offset, 'returned_count': len(selected), 'page_size': PAGE_SIZE,
        'requested_count': requested_count, 'has_more': more,
        'next_offset': end if more else None,
    }
