#!/usr/bin/env bash
# 医患信息数据服务云平台 — 开发环境启动（Git Bash）
#
# 依赖装在 conda 环境 medguard 里，**不是** PATH 上的系统 Python。
# 若环境缺失或依赖不全，脚本会明确报错退出，而不是静默用错解释器。
set -e
cd "$(dirname "$0")/.."

if ! command -v conda >/dev/null 2>&1; then
  echo "[错误] 未找到 conda。请先安装 Anaconda / Miniconda。" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate medguard

if ! python -c "import uvicorn" >/dev/null 2>&1; then
  echo "[错误] conda 环境 medguard 缺少依赖。请先运行：" >&2
  echo "    conda activate medguard && pip install -r requirements.txt" >&2
  exit 1
fi

python -m uvicorn backend.main:app --reload --port 8000
