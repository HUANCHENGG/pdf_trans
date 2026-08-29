@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，请先按 README.md 完成安装。
    pause
    exit /b 1
)

set /p TOKEN=<data\token.txt 2>nul
if "%TOKEN%"=="" (
    echo [错误] 未找到访问令牌 data\token.txt。
    pause
    exit /b 1
)

echo ================================================================
echo   PDF 电子手册翻译工具
echo   浏览器打开:  http://127.0.0.1:8765/?token=%TOKEN%
echo   关闭本窗口即停止服务
echo ================================================================
start "" "http://127.0.0.1:8765/?token=%TOKEN%"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
pause
