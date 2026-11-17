"""按话题和所问字段提供有来源的中文健康科普，不调用模型或患者数据库。

治疗与用药仅解释一般原则，绝不从“高血糖”推断糖尿病或生成个人处方。
资料和来源独立维护；未覆盖的问题继续走既有检索或资料不足路径。
"""
from functools import lru_cache
from pathlib import Path
import re
import yaml


@lru_cache(maxsize=1)
def _catalog():
    try:
        data = yaml.safe_load(Path(__file__).with_suffix('.yaml').read_text(encoding='utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) and data.get('version') == 1 else {}


def lookup(question):
    from backend.knowledge.strategy import DEPARTMENT_QUESTION_SIGNALS
    q = re.sub(r'\s+', '', question)
    fields = []
    if any(s in q for s in DEPARTMENT_QUESTION_SIGNALS):
        fields.append('department')
    if re.search(r'吃什么药|用什么药|哪些药|降糖药|药物治疗', q):
        fields.append('drugs')
    elif re.search(r'如何治疗|怎么治疗|怎样治疗|怎么办|如何处理|怎么处理', q):
        fields.append('treatments')
    if re.search(r'是什么|是啥|有什么用|干什么用|作用|用途', q):
        fields.append('overview')
    catalog = _catalog()
    matches = []
    for key, topic in catalog.get('topics', {}).items():
        aliases = [a for a in topic.get('aliases', []) if a in q]
        if not aliases:
            continue
        requested = fields or (['triage'] if key == 'abdominal_discomfort' else [])
        for field in requested:
            entry = topic.get('fields', {}).get(field)
            if entry:
                matches.append((max(map(len, aliases)), topic, field, entry))
    # 腹部不适同时包含挂科词时，仍先问清症状；不能臆测成某种疾病。
    if not matches:
        topic = catalog.get('topics', {}).get('abdominal_discomfort', {})
        if any(a in q for a in topic.get('aliases', [])):
            matches.append((100, topic, 'triage', topic['fields']['triage']))
    if not matches:
        return None
    _, topic, field, entry = max(matches, key=lambda m: m[0])
    items = [{
        'ref_title': i['title'], 'ref_kind': field,
        'ref_excerpt': i['text'], 'ref_source': i['source'],
        'ref_url': i['url'], 'ref_original_title': i['original_title'],
        'ref_source_type': 'public_health', 'ref_quality_tier': 'public_reference',
        'ref_checked_on': catalog['checked_on'],
    } for i in entry['items']]
    source = '公开机构资料（项目中文整理，未经临床复核）'
    return {
        'intent': topic['intent'], 'entity': topic['entity'], 'field': field,
        'interpretation': {'source': source, 'title': entry['title'],
                           'text': entry['summary'], 'items': items},
        'advice': {'source': source, 'text': entry['advice'], 'actions': [], 'urgent': False},
    }
