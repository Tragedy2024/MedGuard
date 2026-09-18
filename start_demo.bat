@echo off
setlocal enabledelayedexpansion
REM 一键演示启动 —— 单进程、单端口（Windows，双击即可）
REM
REM   start_demo.bat            本机演示
REM   start_demo.bat --lan      同时允许局域网访问（手机、另一台电脑都能开）
REM   start_demo.bat --build    强制重新构建前端
REM
REM 后端会顺带托管前端构建产物（frontend\dist），所以只有**一个**进程：
REM 少开一个终端，而且前后端同源 —— 浏览器不产生跨域预检，换端口、用局域网
REM IP 访问都不会撞 CORS。
REM
REM 逻辑与 start_demo.sh 一致；两者要保持同步，改一个记得改另一个。
REM 本文件以 **GBK** 保存：cmd 按 OEM 代码页读批处理，存成 UTF-8 的话中文
REM 会被拆成乱码并当作命令执行（实测报一串"不是内部或外部命令"）。
cd /d "%~dp0"

set PORT=8000
set LAN=0
set BUILD=0

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--lan"   set LAN=1
if /i "%~1"=="--build" set BUILD=1
if /i "%~1"=="-h"      goto usage
if /i "%~1"=="--help"  goto usage
shift
goto parse
:parsed

REM ── 1. 找 Python 解释器 ────────────────────────────────────────
REM
REM 依赖装在 conda 环境 medguard 里，**不是** PATH 上的系统 Python：后者
REM 没有 sqlglot，服务起得来但一查就报 ModuleNotFoundError —— 这类故障在
REM 演示现场很难当场排查。所以逐个候选**验依赖**，验不过就继续找。
REM
REM 不能只靠 `conda activate`：非交互 cmd 里它会静默失败，然后 python 落到
REM 别的环境上（实测就是栽在这里）。每一处候选都必须真跑一次 import。
set "PY="

if defined MEDGUARD_PYTHON call :try "%MEDGUARD_PYTHON%"
if not defined PY for %%p in (python.exe) do call :try "%%~$PATH:p"
if not defined PY (
  for /f "delims=" %%b in ('conda info --base 2^>nul') do call :try "%%b\envs\medguard\python.exe"
)
if not defined PY call :try "%USERPROFILE%\anaconda3\envs\medguard\python.exe"
if not defined PY call :try "%USERPROFILE%\miniconda3\envs\medguard\python.exe"
if not defined PY call :try "%ProgramData%\Anaconda3\envs\medguard\python.exe"

if not defined PY (
  echo [错误] 找不到装了依赖^(fastapi / uvicorn / sqlglot^)的 Python。
  echo         请先建好环境：
  echo             conda create -n medguard python=3.12
  echo             conda activate medguard ^&^& pip install -r backend\requirements.txt
  echo         环境已存在但仍报此错，就直接指定解释器：
  echo             set MEDGUARD_PYTHON=D:\Anaconda\envs\medguard\python.exe
  exit /b 1
)

REM ── 2. 前端构建产物 ───────────────────────────────────────────
if "%BUILD%"=="1" goto do_build
if exist "frontend\dist\index.html" goto ready
:do_build
echo [构建] 正在构建前端...
where npm >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 npm。若 Node 装在非 PATH 位置^(例如 D:\nodejs^)，
  echo         请先把它的目录加进 PATH 再重跑本脚本。
  exit /b 1
)
pushd frontend
if not exist node_modules call npm install
call npm run build
popd
if not exist "frontend\dist\index.html" (
  echo [错误] 前端构建失败，未生成 frontend\dist\index.html
  exit /b 1
)
:ready

REM ── 3. 起服务 ─────────────────────────────────────────────────
if "%LAN%"=="1" (set HOST=0.0.0.0) else (set HOST=127.0.0.1)

echo.
echo   医盾 MedGuard · 演示
echo   --------------------------------------------
echo   地址    http://localhost:%PORT%
if "%LAN%"=="1" (
  for /f "delims=" %%i in ('"!PY!" -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print(s.getsockname()[0])" 2^>nul') do set IP=%%i
  if defined IP echo   局域网  http://!IP!:%PORT%   ^<- 手机/另一台电脑可打开
)
echo   账号    admin / doctor / patient    口令 medguard
echo   停止    Ctrl+C
echo.

REM 等端口真起来再开浏览器，否则先看到一个连接失败页
start "" powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 40;$i++){try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',%PORT%);Start-Process 'http://localhost:%PORT%';break}catch{Start-Sleep -Milliseconds 250}}"

cd backend
REM --no-access-log：GET 端点把整个签名令牌放在查询串里，而 uvicorn 默认
REM 把完整请求行写进日志 —— 那等于把可重放的凭证打进终端与录屏。
REM 需要排查请求时设 MEDGUARD_ACCESS_LOG=1。
set "ACCESS_LOG_FLAG=--no-access-log"
if "%MEDGUARD_ACCESS_LOG%"=="1" set "ACCESS_LOG_FLAG="
"!PY!" -m uvicorn backend.main:app --host %HOST% --port %PORT% !ACCESS_LOG_FLAG!
goto :eof

REM ── 子过程：验证某个解释器是否齐活 ────────────────────────────
:try
if defined PY goto :eof
if "%~1"=="" goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import fastapi,uvicorn,sqlglot" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
goto :eof

:usage
echo 用法：
echo   start_demo.bat            本机演示
echo   start_demo.bat --lan      同时允许局域网访问
echo   start_demo.bat --build    强制重新构建前端
exit /b 0
