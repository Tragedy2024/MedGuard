"""准备 MedGuard 的完整演示数据（OpenCMKG 派生知识 + 演示业务库）。

为什么需要这个脚本：OpenCMKG 的许可为「仅学术研究，不得商用」，且没有
明确的派生数据再分发授权，因此仓库**不提交**原始文件、派生 JSON 和向量
缓存。新克隆默认只有合成演示数据（`*.synthetic.json`）；要展示完整的
疾病事实卡 / 症状导诊 / OpenCMKG 第二检索源，需要使用者自行下载固定版本
的原始三元组并在本地生成。

用法：

    # 通过代理下载、校验、生成三份 JSON（不重建业务库）
    python scripts/prepare_demo_data.py --download --proxy http://127.0.0.1:7897

    # 已有本地 triples.txt
    python scripts/prepare_demo_data.py --triples data/opencmkg/triples.txt

    # 生成后重建演示业务库（会先备份旧库）
    python scripts/prepare_demo_data.py --triples data/opencmkg/triples.txt --reset-db

    # 只看会做什么，不落盘
    python scripts/prepare_demo_data.py --download --dry-run

**本脚本只把数据写到本地 `backend/data/` 与 `backend/demo/`（两者都已被
.gitignore 排除），不会提交任何原始或派生数据。**
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]      # backend/
DEFAULT_TRIPLES = PROJECT_ROOT / "data" / "opencmkg" / "triples.txt"
DEFAULT_OUTPUT = PROJECT_ROOT / "demo"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_dataset_tables.py"
BUSINESS_DB = PROJECT_ROOT / "data" / "regional_health.db"

# 与 build_dataset_tables.py 保持同一个固定版本/哈希。
TRIPLES_SHA256 = "1d6e6242baf20a7c9c2044809a53969ffb73e3248ac9a7911f8b053b06438632"
SOURCE_VERSION = "61ff8aa8635b7b4c4c831cc56108e4116ecc3c01"
SOURCE_URL = (
    "https://raw.githubusercontent.com/RuiqingDing/OpenCMKG/"
    f"{SOURCE_VERSION}/triples.txt"
)

LICENSE_NOTICE = (
    "OpenCMKG 许可：仅用于学术研究，不得商用；未明确授予派生数据再分发权。\n"
    "本脚本只把原始/派生数据写到本地（backend/data、backend/demo），"
    "不会提交进仓库。"
)


def sha256_file(path: os.PathLike | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha(path: os.PathLike | str,
               expected: str | None = None) -> str:
    """校验固定版本；不符直接退出，避免用错版本生成知识库。

    `expected` 在调用时取模块常量（而不是定义时绑定默认参数），
    这样测试和替换版本时都能 monkeypatch `TRIPLES_SHA256`。
    """
    expected = expected or TRIPLES_SHA256
    actual = sha256_file(path)
    if actual != expected:
        raise SystemExit(
            f"[错误] 原始文件 SHA-256 不符。\n"
            f"  期望: {expected}\n  实际: {actual}\n"
            f"  请使用固定版本：{SOURCE_URL}"
        )
    return actual


def download(url: str, dest: Path, proxy: str | None = None) -> None:
    """下载到 .part 后校验、再原子替换；校验失败不留下半成品。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({
            "http": proxy,
            "https": proxy,
        }))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(url, timeout=180) as response, \
                open(partial, "wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
        verify_sha(partial)
        os.replace(partial, dest)
    finally:
        if partial.exists():
            partial.unlink()


def build_docs(triples: Path, output_dir: Path, *,
               export_docs: bool = True) -> None:
    command = [
        sys.executable,
        str(BUILD_SCRIPT),
        str(triples),
        "--output-dir",
        str(output_dir),
    ]
    if export_docs:
        command.append("--export-docs")
    subprocess.run(command, check=True, cwd=str(PROJECT_ROOT))


def reset_demo_db() -> None:
    """重建演示业务库；先备份旧库，避免把演示状态一起删掉。"""
    if BUSINESS_DB.exists():
        backup = BUSINESS_DB.with_suffix(".db.bak")
        shutil.copy2(BUSINESS_DB, backup)
        print(f"[db] 已备份旧库 → {backup}")
    subprocess.run([sys.executable, "-m", "demo.seed"],
                   check=True, cwd=str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="准备 MedGuard 完整演示数据（OpenCMKG 本地派生）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=LICENSE_NOTICE,
    )
    parser.add_argument("--triples", type=Path, default=DEFAULT_TRIPLES,
                        help=f"本地 triples.txt（默认 {DEFAULT_TRIPLES}）")
    parser.add_argument("--download", action="store_true",
                        help="下载固定版本 triples.txt 到 --triples 路径")
    parser.add_argument("--url", default=SOURCE_URL,
                        help="下载地址（默认固定 commit 的 raw URL）")
    parser.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY"),
                        help="下载代理，例如 http://127.0.0.1:7897")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                        help=f"派生 JSON 输出目录（默认 {DEFAULT_OUTPUT}）")
    parser.add_argument("--no-docs", action="store_true",
                        help="只生成疾病/症状表，不生成 rag_documents.json")
    parser.add_argument("--reset-db", action="store_true",
                        help="生成后重建演示业务库（会先备份旧库）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划，不下载、不生成、不重建")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(LICENSE_NOTICE)
    print()

    if args.dry_run:
        print("[dry-run] 计划：")
        if args.download:
            print(f"  下载 {args.url} → {args.triples}")
        else:
            print(f"  使用本地文件 {args.triples}")
        print(f"  校验 SHA-256（{TRIPLES_SHA256[:16]}…）")
        print(f"  生成派生 JSON → {args.output_dir}"
              f"（{'含' if not args.no_docs else '不含'} rag_documents.json）")
        if args.reset_db:
            print(f"  重建演示业务库 {BUSINESS_DB}（先备份）")
        print("[dry-run] 未写入任何文件。")
        return 0

    triples = args.triples
    if args.download:
        if triples.exists() and sha256_file(triples) == TRIPLES_SHA256:
            print(f"[download] 已存在且哈希正确，跳过：{triples}")
        else:
            print(f"[download] 正在下载：{args.url}")
            download(args.url, triples, args.proxy)
            print(f"[download] 完成：{triples}")
    elif not triples.exists():
        raise SystemExit(
            f"[错误] 找不到 {triples}。\n"
            f"  请加 --download，或先把固定版本文件放到该路径：\n"
            f"  {SOURCE_URL}"
        )

    digest = verify_sha(triples)
    print(f"[verify] SHA-256 通过：{digest[:16]}…")

    build_docs(triples, args.output_dir, export_docs=not args.no_docs)
    print(f"[build] 派生 JSON 已生成：{args.output_dir}")

    if args.reset_db:
        reset_demo_db()
        print(f"[db] 演示业务库已重建：{BUSINESS_DB}")

    print()
    print("完成。下一步：")
    print("  1) 启动后端：python -m uvicorn backend.main:app --port 8000")
    print("  2) 健康检查：curl http://localhost:8000/api/health")
    print("  3) 若要用第二检索源，设置 MEDGUARD_RAG_OPENCMKG=1")
    print("  提醒：OpenCMKG 数据仅限学术研究，不要提交或对外分发。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
