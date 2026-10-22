"""RAG 延迟/内存验收基准（评审 A2，2026-10）。

测量方式（每项在独立进程里跑一次，避免互相污染）：

    python scripts/bench_rag_latency.py --cold    冷启动：首次检索总耗时
    python scripts/bench_rag_latency.py --warm    热查询：35 次检索 P50/P95/max
    python scripts/bench_rag_latency.py --bm25    BM25 降级路径（不开 BGE）热查询
    python scripts/bench_rag_latency.py --memory  进程 RSS 增量 + 向量矩阵大小

机器配置与缓存状态随结果一并打印——**只作本机验收数据**，不作普适结论；
复跑时应记录：机器配置、HF 缓存是否存在、向量缓存文件是否存在、次数与分位数。
"""
import argparse
import os
import statistics
import sys
import time

# 必须在导入 backend 之前：本基准要真实加载 BGE 与向量缓存
os.environ["MEDGUARD_TESTING"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_QUERIES = [
    "我想了解帕金森病", "荨麻疹怎么治", "最近老头晕是怎么回事",
    "睡不着怎么办", "高血压的饮食禁忌有哪些", "腰疼应该看哪个科室",
    "咳嗽一周了要紧吗", "胃不舒服是什么原因",
]


def _frame() -> str:
    """机器/缓存状态摘要（验收数据必须带上上下文）。"""
    import socket
    import platform
    lines = [
        f"机器: {platform.platform()}, CPU={os.cpu_count()} 核",
        f"Python: {platform.python_version()}",
        f"主机: {socket.gethostname()}",
    ]
    from backend.knowledge.hybrid import _dense_cache_path, _model_cached_locally
    from backend import config
    for label in ("yaml", "opencmkg"):
        p = _dense_cache_path(label)
        lines.append(f"向量缓存 {label}: {'存在 ' + str(os.path.getsize(p) // 1024) + ' KB' if os.path.exists(p) else '缺失'}")
    lines.append(f"BGE 本地快照: {'有' if _model_cached_locally(config.RAG_DENSE_MODEL) else '无'}")
    return "\n".join(lines)


def _rss_mb() -> float:
    import psutil
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024


def _timed(name: str, fn, rounds: int = 5) -> float:
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return times[len(times) // 2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["cold", "warm", "bm25", "memory"])
    args = ap.parse_args()

    from backend import smart_doctor as sd
    from backend.knowledge import hybrid

    kb = sd.load_knowledge()

    if args.mode == "cold":
        t0 = time.perf_counter()
        hybrid.search(kb, _QUERIES[0])
        total_ms = (time.perf_counter() - t0) * 1000
        print(_frame())
        print(f"冷启动（缓存加载+模型加载+建索引，首次检索）: {total_ms:.0f} ms")

    elif args.mode in ("warm", "bm25"):
        t0 = time.perf_counter()
        hybrid.search(kb, _QUERIES[0])
        warmup = (time.perf_counter() - t0) * 1000
        times = []
        for rep in range(5):
            for q in _QUERIES:
                t = time.perf_counter()
                hybrid.search(kb, q)
                times.append((time.perf_counter() - t) * 1000)
        times.sort()
        n = len(times)
        print(_frame())
        print(f"预热（首次含加载）: {warmup:.0f} ms")
        print(f"热查询 {n} 次: P50={times[n // 2]:.1f} ms | "
              f"P95={times[int(n * 0.95) - 1]:.1f} ms | "
              f"max={times[-1]:.1f} ms | mean={statistics.mean(times):.1f} ms")
        if args.mode == "bm25":
            print("（MEDGUARD_RAG_DENSE=0：纯 BM25 降级路径）")

    else:  # memory
        base = _rss_mb()
        hybrid.search(kb, _QUERIES[0])          # 触发检索器构建
        after = _rss_mb()
        r = hybrid.get_retriever(kb)
        vec_mb = 0
        for idx in (r._dense, r._extra_dense if hasattr(r, "_extra_dense") else None):
            if idx is not None and idx._np is not None:
                vec_mb += idx._np.nbytes / 1024 / 1024
        print(_frame())
        print(f"进程 RSS 增量（导入→检索器就绪）: {after - base:.0f} MB")
        print(f"主/辅向量矩阵合计: {vec_mb:.1f} MB（float32）")
        print(f"检索器就绪后总 RSS: {after:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())