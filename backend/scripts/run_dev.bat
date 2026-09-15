@echo off
REM 医患信息数据服务云平台 — 开发环境启动（Windows）
REM
REM 依赖装在 conda 环境 medguard 里，**不是** PATH 上的系统 Python。
REM 若环境缺失或依赖不全，脚本会明确报错退出，而不是静默用错解释器。
cd /d %~dp0..

call conda activate medguard >nul 2>nul
python -c "import uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [错误] 无法在 conda 环境 medguard 中导入 uvicorn。
  echo         请先运行:
  echo             conda create -n medguard python=3.12
  echo             conda activate medguard ^&^& pip install -r requirements.txt
  exit /b 1
)

python -m uvicorn backend.main:app --reload --port 8000
