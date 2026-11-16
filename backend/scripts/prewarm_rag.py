"""预热混合检索：加载 BGE、构建语料向量缓存，并跑一条示例查询。

为什么需要：BGE 首次加载 + 91 条 YAML 语料编码在 CPU 上约 10 秒；
演示时第一问不该吃这个延迟。演示前跑一次本脚本，向量缓存落到
`data/dense_*.npy`（gitignore），后续查询直接读缓存。

用法：
    python scripts/prewarm_rag.py
    python scripts/prewarm_rag.py --query "我睡不着怎么办" --top-k 3
    python scripts/prewarm_rag.py --opencmkg   # 临时启用第二知识源预热

第二知识源默认关闭；`--opencmkg` 只影响本进程，不修改 .env。
OpenCMKG 数据仅限学术研究，预热产物同样不要提交进仓库。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# 允许 `python scripts/prewarm_rag.py` 直接运行时导入 backend 包。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="预热 MedGuard 混合检索（BGE + BM25 + RRF）")
    parser.add_argument("--query", default="血糖结果怎么看",
                        help="预热时使用的示例问句")
    parser.add_argument("--top-k", type=int, default=3, help="返回条数")
    parser.add_argument("--opencmkg", action="store_true",
                        help="本进程临时启用 OpenCMKG 第二知识源")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.opencmkg:
        # 必须在下面 import backend 之前设置：config 在导入时读取该变量。
        os.environ["MEDGUARD_RAG_OPENCMKG"] = "1"
        print("[info] 本进程临时启用 OpenCMKG 第二知识源。", flush=True)

    from backend import smart_doctor
    from backend.knowledge import hybrid

    kb = smart_doctor.load_knowledge()
    if not kb:
        print("[错误] 知识库为空，无法预热。", flush=True)
        return 1

    started = time.perf_counter()
    hits = hybrid.search(kb, args.query, top_k=args.top_k)
    elapsed = time.perf_counter() - started
    status = hybrid.runtime_status()

    print(f"[prewarm] 示例问句：{args.query}")
    print(f"[prewarm] 检索模式：{status.get('mode')}（status={status.get('status')}）")
    print(f"[prewarm] 主知识源文档：{status.get('primary_documents')} 条；"
          f"第二知识源：{status.get('extra_documents')} 条")
    print(f"[prewarm] 首次检索耗时：{elapsed:.2f}s")
    if status.get("reason"):
        print(f"[prewarm] 降级原因：{status['reason']}")
    for doc, score in hits:
        print(f"  - {doc.name}（{doc.kind}）score={score:.3f}")

    if status.get("status") != "full":
        print("[warn] 当前不是 BGE + BM25 全量模式；演示脚本里不要宣称混合检索已生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
