"""OpenCMKG 检索文档适配器（第二知识源）。

分工（与 backend/backend/knowledge/corpus.py 的第一知识源 YAML 对应）：

    第一知识源     smart_knowledge.yaml      约 91 条，人工整理，可信度高
    第二知识源     demo/rag_documents.json   约 1.5 万条，OpenCMKG 清洗产物

本模块只做一件事：读 `rag_documents.json`（由 build_dataset_tables.py 的
`--export-docs` 生成）并展平成统一的 corpus.Doc，供混合检索使用。

**安全边界（2026-09 设计评审）**：
- OpenCMKG 只贡献**事实召回**（疾病/症状/检查/科室），不参与直接诊断；
  检索命中的条目最终要回查 `disease_facts.json` / `symptom_departments.json`
  的完整事实卡，LLM 只能带引用地解释证据。
- 检索文档**不包含药物与治疗字段**——第一阶段这两类不参与生成；
  `disease_facts.json` 里的它们只用于界面事实卡展示。
- 文档 ID 稳定（`opencmkg:disease:冠心病`），作为证据引用与回查的锚点。
"""
import json
import os
import threading
from typing import List, Optional

from backend import config
from backend.knowledge import corpus

_DOCS_FILE = os.path.join(config.DEMO_DIR, "rag_documents.json")

_LOCK = threading.Lock()
_CACHE: Optional[List[corpus.Doc]] = None


def load_docs() -> List[corpus.Doc]:
    """读检索文档并展平成 Doc 列表；读取失败返回空列表（检索自动只走 YAML）。"""
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        docs: List[corpus.Doc] = []
        try:
            with open(_DOCS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for e in data.get("documents", []):
                name = e.get("name") or e.get("key") or ""
                if not name:
                    continue
                docs.append(corpus.Doc(
                    id=e.get("id") or f"opencmkg:{e.get('kind', 'disease')}:{name}",
                    kind=e.get("kind", "disease"),
                    key=e.get("key") or name,
                    name=name,
                    aliases=tuple(e.get("aliases") or [name]),
                    text=e.get("text") or "",
                    source="OpenCMKG",
                    source_title="OpenCMKG（开源中文医学知识图谱）",
                    source_version=e.get("source_version") or "",
                    license=e.get("license") or "仅学术研究，不得商用",
                    quality_tier="opencmkg",
                ))
        except Exception:  # noqa: BLE001 - 文件缺失/损坏/形状异常都降级，
            # 第二知识源不能以任何方式连累第一知识源（含 JSON 顶层非对象等形状错误）
            docs = []
        _CACHE = docs
        return docs


def reset() -> None:
    """测试或热更新数据后清缓存。"""
    global _CACHE
    with _LOCK:
        _CACHE = None