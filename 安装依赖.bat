@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
echo.
echo 依赖安装完成，按任意键退出...
pause >nul
