"""智慧医生的知识检索层（RAG 的检索那一步）。

    corpus.py     把 smart_knowledge.yaml 展平成统一文档
    tokenize.py   jieba 分词 + 用户词典（词典来自语料 aliases）
    retrieval.py  BM25 排序

**用途只有一个：为生成提供相关段落。** 也就是 RAG 里的 R。

它**不**参与"判断用户在问哪个条目"——那件事由 smart_doctor 的别名字面匹配
负责（`_find_entity` 等）。两件事的正确索引口径本来就不一样，别混：

    实体识别 → 索引只放名字（正文会把「我肾怎么样」误配到「水肿」）
    相关段落 → 索引必须含正文（要的就是"内容相关"，不是"名字相同"）

2026-09-21 早先那次实测（`.scratch/rag-ux/`）得出的"检索零增量"结论，
**只针对实体识别**，不适用于本模块。

本层零数据库访问；检索本身不调模型（只有拿到上下文之后的生成才调）。
"""
from backend.knowledge.corpus import (KIND_DEPARTMENT, KIND_DISEASE,
                                      KIND_LAB, KIND_LABELS, KIND_MEDICATION,
                                      KIND_SYMPTOM, Doc, build_docs)

__all__ = ["Doc", "build_docs", "KIND_LABELS", "KIND_LAB", "KIND_MEDICATION",
           "KIND_SYMPTOM", "KIND_DISEASE", "KIND_DEPARTMENT"]
