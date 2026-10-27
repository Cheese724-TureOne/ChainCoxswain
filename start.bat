@echo off
rem ChainCoxswain 一键启动（Windows）
chcp 65001 >nul
cd /d "%~dp0"

rem 加载 .env（若存在）；注释行以 # 开头
if exist .env for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"

where python >nul 2>nul
if errorlevel 1 (
  echo [ChainCoxswain] 未找到 python，请先安装 Python 3.9+ 并加入 PATH
  pause
  exit /b 1
)

echo [ChainCoxswain] 启动后端 http://127.0.0.1:%CHAINCOXSWAIN_PORT%
if not defined CHAINCOXSWAIN_PORT set CHAINCOXSWAIN_PORT=8765
echo 前端：直接用浏览器打开 index.html（file:// 离线可演示）
echo 停止：关闭本窗口 或 Ctrl+C
python backend\server.py
pause
