# -*- coding: utf-8 -*-
"""检索质量评测（阶段 2，2026-09 设计评审）。

对 `tests/data/retrieval_gold.jsonl` 的金标问法（四类：yaml / colloquial /
no_evidence / emergency）跑指定检索配置，统计：

    Recall@3 / Recall@5   覆盖类问题的期望条目命中率
    拒答率                 无关问题返回空证据的比例（越高越诚实）
    误召回                 无关问题返回了条目的数量与例子
    急症检出率             红旗规则层对急症表述的检出（与检索正交）

配置（env 在导入 backend 前设置，与生产开关一致）：
    bm25        MEDGUARD_RAG_DENSE=0 MEDGUARD_RAG_OPENCMKG=0   纯 BM25（91 条）
    yaml_hybrid MEDGUARD_RAG_DENSE=1 MEDGUARD_RAG_OPENCMKG=0   YAML + BGE
    ocm_bm25    MEDGUARD_RAG_DENSE=0 MEDGUARD_RAG_OPENCMKG=1   BM25 + OpenCMKG
    full        MEDGUARD_RAG_DENSE=1 MEDGUARD_RAG_OPENCMKG=1   全量混合

用法：
    python scripts/eval_retrieval.py --config bm25
    python scripts/eval_retrieval.py --all          # 四路对比（子进程）
"""
import argparse
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
GOLD = os.path.join(_BACKEND, "tests", "data", "retrieval_gold.jsonl")

_CONFIGS = {
    "bm25":        {"MEDGUARD_RAG_DENSE": "0", "MEDGUARD_RAG_OPENCMKG": "0"},
    "yaml_hybrid": {"MEDGUARD_RAG_DENSE": "1", "MEDGUARD_RAG_OPENCMKG": "0"},
    "ocm_bm25":    {"MEDGUARD_RAG_DENSE": "0", "MEDGUARD_RAG_OPENCMKG": "1"},
    "full":        {"MEDGUARD_RAG_DENSE": "1", "MEDGUARD_RAG_OPENCMKG": "1"},
}


def _load_gold() -> list:
    rows = []
    with open(GOLD, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_one(config: str) -> dict:
    """跑单个配置；返回结构化指标。"""
    from backend import smart_doctor
    from backend.knowledge import corpus, hybrid, opencmkg
    from backend.redflags import check as redflag_check

    kb = smart_doctor.load_knowledge()
    hybrid.reset()
    opencmkg.reset()

    # 期望名必须能在语料里解析（拼写防护；对照并集）
    known = set()
    for d in corpus.build_docs(kb):
        known.add(d.name)
        known.update(d.aliases)
    for d in opencmkg.load_docs():
        known.add(d.name)
        known.update(d.aliases)

    rows = _load_gold()
    missing = sorted({name for r in rows for name in r["expect"]
                      if name not in known})
    if missing:
        raise SystemExit(f"[错误] 金标期望条目在语料中不存在（拼写问题）：{missing}")

    TOP = 5
    stats = {c: {"n": 0, "h3": 0, "h5": 0, "false": 0, "examples": []}
             for c in ("yaml", "colloquial", "no_evidence")}
    emerg = {"n": 0, "detected": 0, "miss": []}

    for r in rows:
        cat = r["category"]
        if cat == "emergency":
            emerg["n"] += 1
            flags = redflag_check(r["q"])
            want = r.get("redflag", "")
            ok = any(f.label == want for f in flags) if want else bool(flags)
            if ok:
                emerg["detected"] += 1
            else:
                emerg["miss"].append(r["q"])
            continue
        hits = hybrid.search(kb, r["q"], top_k=TOP)
        names = [d.name for d, _ in hits]
        s = stats[cat]
        s["n"] += 1
        if cat == "no_evidence":
            if names:
                s["false"] += 1
                if len(s["examples"]) < 3:
                    s["examples"].append({"q": r["q"], "got": names[:3]})
        else:
            if any(n in r["expect"] for n in names[:3]):
                s["h3"] += 1
            if any(n in r["expect"] for n in names):
                s["h5"] += 1

    cov = stats["yaml"]["n"] + stats["colloquial"]["n"]
    cov_h3 = stats["yaml"]["h3"] + stats["colloquial"]["h3"]
    cov_h5 = stats["yaml"]["h5"] + stats["colloquial"]["h5"]
    return {
        "config": config,
        "coverage": {"n": cov,
                     "recall@3": round(cov_h3 / cov, 4) if cov else 0,
                     "recall@5": round(cov_h5 / cov, 4) if cov else 0},
        "by_category": {
            "yaml": {"n": stats["yaml"]["n"],
                     "recall@3": round(stats["yaml"]["h3"] / max(stats["yaml"]["n"], 1), 4),
                     "recall@5": round(stats["yaml"]["h5"] / max(stats["yaml"]["n"], 1), 4)},
            "colloquial": {"n": stats["colloquial"]["n"],
                           "recall@3": round(stats["colloquial"]["h3"] / max(stats["colloquial"]["n"], 1), 4),
                           "recall@5": round(stats["colloquial"]["h5"] / max(stats["colloquial"]["n"], 1), 4)},
        },
        "no_evidence": {
            "n": stats["no_evidence"]["n"],
            "empty_rate": round((stats["no_evidence"]["n"] - stats["no_evidence"]["false"])
                                / max(stats["no_evidence"]["n"], 1), 4),
            "false_recalls": stats["no_evidence"]["false"],
            "examples": stats["no_evidence"]["examples"],
        },
        "emergency": {"n": emerg["n"], "detected": emerg["detected"],
                      "miss": emerg["miss"]},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=sorted(_CONFIGS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（供汇总）")
    args = ap.parse_args()

    if args.all:
        # 子进程逐配置运行，避免进程内开关串扰（tokenize 词典只认第一次）
        for name in _CONFIGS:
            env = dict(os.environ)
            env.update(_CONFIGS[name])
            env["MEDGUARD_TESTING"] = "0"
            r = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--config", name, "--json"],
                cwd=_BACKEND, env=env, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[{name}] 失败：{r.stderr[-400:]}")
                continue
            # 子进程若发生降级（如 BGE 加载失败回退 BM25），把 [rag] 日志透传出来，
            # 避免"配置标了 dense 但实际跑的是 BM25"这种静默情况被忽略。
            # 注意：hybrid 的 print 走 stdout，子进程进度条走 stderr，两处都要扫。
            for ln in (r.stdout + "\n" + r.stderr).splitlines():
                if "[rag]" in ln:
                    print(f"[{name}] {ln}", file=sys.stderr)
            res = json.loads(r.stdout.strip().splitlines()[-1])
            print(json.dumps(res, ensure_ascii=False))
        return

    cfg = args.config or "bm25"
    os.environ.update(_CONFIGS[cfg])
    os.environ["MEDGUARD_TESTING"] = "0"   # 评测必须真实加载 BGE，显式关掉测试隔离
    if not args.json:
        print(f"===== 配置: {cfg} =====")
    res = run_one(cfg)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # 确保能 import backend 包（scripts/ 的上一级是 backend 根）
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    main()