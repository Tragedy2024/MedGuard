#!/usr/bin/env bash
#
# 一键演示启动 —— 单进程、单端口（Git Bash / macOS / Linux）
#
#   ./start_demo.sh              本机演示
#   ./start_demo.sh --lan        同时允许局域网访问（手机、另一台电脑都能开）
#   ./start_demo.sh --build      强制重新构建前端
#   ./start_demo.sh --no-open    不自动开浏览器
#
# 后端会顺带托管前端构建产物（frontend/dist），所以只有**一个**进程：
# 少开一个终端，而且前后端同源 —— 浏览器不产生跨域预检，换端口、用局域网
# IP 访问都不会撞 CORS。演示现场因此少一个会翻车的环节。
#
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
LAN=0; BUILD=0; OPEN=1
for a in "$@"; do
  case "$a" in
    --lan)     LAN=1 ;;
    --build)   BUILD=1 ;;
    --no-open) OPEN=0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "未知参数：$a（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

# ── 1. 找 Python ──────────────────────────────────────────────
#
# 依赖装在 conda 环境 medguard 里，**不是** PATH 上的系统 Python：后者没有
# sqlglot，服务起得来但一查就报 ModuleNotFoundError —— 这类故障在演示现场
# 很难当场排查。所以这里逐个候选**验依赖**，验不过就继续找，全都不行才报错。
#
# 不能只靠 `conda activate`：非交互 shell 里它可能静默失败，然后 python 落到
# 别的环境上。这也是为什么下面每一处都要跑一次 import 检查。
_ok() { [ -n "${1:-}" ] && [ -x "$1" ] && "$1" -c "import fastapi, uvicorn, sqlglot" >/dev/null 2>&1; }

PY=""
for cand in \
    "${MEDGUARD_PYTHON:-}" \
    "$(command -v python3 2>/dev/null || true)" \
    "$(command -v python 2>/dev/null || true)"
do
  if [ -z "$PY" ] && _ok "$cand"; then PY="$cand"; fi
done

# conda 环境 medguard：先问 conda 的 base 在哪，再拼 envs 路径；同时兜几个常见位置
if [ -z "$PY" ] && command -v conda >/dev/null 2>&1; then
  BASE="$(conda info --base 2>/dev/null || true)"
  for cand in "${BASE:+$BASE/envs/medguard/python.exe}" \
              "${BASE:+$BASE/envs/medguard/bin/python}" \
              "$HOME/anaconda3/envs/medguard/python.exe" \
              "$HOME/miniconda3/envs/medguard/python.exe"; do
    if [ -z "$PY" ] && _ok "$cand"; then PY="$cand"; fi
  done
fi

if [ -z "$PY" ]; then
  echo "[错误] 找不到装了依赖（fastapi / uvicorn / sqlglot）的 Python。" >&2
  echo "       请先建好环境：" >&2
  echo "           conda create -n medguard python=3.12" >&2
  echo "           conda activate medguard && pip install -r backend/requirements.txt" >&2
  echo "       环境已存在但仍报此错，就直接指定解释器：" >&2
  echo "           MEDGUARD_PYTHON=/d/Anaconda/envs/medguard/python.exe ./start_demo.sh" >&2
  exit 1
fi

# ── 2. 前端构建产物 ───────────────────────────────────────────
#
# 只在缺失或显式 --build 时构建。演示现场不该把时间花在等 Vite 打包上。
if [ "$BUILD" = 1 ] || [ ! -f frontend/dist/index.html ]; then
  echo "[构建] 正在构建前端…"
  if ! command -v npm >/dev/null 2>&1; then
    echo "[错误] 找不到 npm，无法构建前端。" >&2
    echo "       若 Node 装在非 PATH 位置（例如 Windows 的 D:\\nodejs），" >&2
    echo "       先把它加进 PATH 再跑本脚本：" >&2
    echo "           export PATH=\"/d/nodejs:\$PATH\"" >&2
    exit 1
  fi
  ( cd frontend && { [ -d node_modules ] || npm install; } && npm run build )
fi

# ── 3. 起服务 ─────────────────────────────────────────────────
HOST="127.0.0.1"
[ "$LAN" = 1 ] && HOST="0.0.0.0"
URL="http://localhost:${PORT}"

lan_ip() {
  # 连一个外部地址让内核挑出口网卡——UDP 不会真发包，只是问路由表。
  "$PY" - <<'PYEOF' 2>/dev/null || true
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("8.8.8.8", 80))
    print(s.getsockname()[0])
finally:
    s.close()
PYEOF
}

echo
echo "  ╭──────────────────────────────────────────────╮"
echo "  │  医盾 MedGuard · 演示                        │"
echo "  ╰──────────────────────────────────────────────╯"
echo "    地址    ${URL}"
if [ "$LAN" = 1 ]; then
  IP="$(lan_ip)"
  [ -n "$IP" ] && echo "    局域网  http://${IP}:${PORT}   ← 手机/另一台电脑可打开"
fi
echo "    账号    admin / doctor / patient    口令 medguard"
echo "    停止    Ctrl+C"
echo

if [ "$OPEN" = 1 ]; then
  # 等端口真的起来再开浏览器，否则会先看到一个连接失败页
  (
    for _ in $(seq 1 40); do
      if "$PY" -c "
import socket,sys
s=socket.socket(); s.settimeout(0.3)
sys.exit(0 if s.connect_ex(('127.0.0.1', ${PORT}))==0 else 1)
" 2>/dev/null; then
        case "$(uname -s)" in
          Darwin) open "$URL" ;;
          Linux)  xdg-open "$URL" >/dev/null 2>&1 || true ;;
          *)      cmd.exe /c start "" "$URL" >/dev/null 2>&1 || true ;;
        esac
        break
      fi
      sleep 0.25
    done
  ) &
fi

# 不加 --reload：演示时不需要热重载，而 watchfiles 在 Windows 上实测会漏掉
# 后端源码改动（只对测试文件触发过），留着反而制造"改了没生效"的困惑。
# 必须 cd 进 backend/：Python 包是 backend/backend/，从仓库根跑 import 不到。
cd backend
# `--no-access-log`：GET 类端点把**整个签名令牌**放在查询串里
# （见 frontend/src/api/scope.ts），而 uvicorn 默认把完整请求行写进日志——
# 那等于把可重放的 8 小时凭证打进终端、日志文件和录屏。
# 需要排查请求时改为 `MEDGUARD_ACCESS_LOG=1` 临时打开。
ACCESS_LOG_FLAG="--no-access-log"
[ "${MEDGUARD_ACCESS_LOG:-0}" = "1" ] && ACCESS_LOG_FLAG=""
exec "$PY" -m uvicorn backend.main:app --host "$HOST" --port "$PORT" $ACCESS_LOG_FLAG
