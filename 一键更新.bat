@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   持仓净值曲线 —— 全历史重建
echo   会把最新数据写进 data.js / history，浏览器下次打开自动接续
echo ============================================================
echo.

set PY=C:\Users\Maple\.workbuddy\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python

"%PY%" "%~dp0scripts\update_data.py" %*
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
  echo [完成] data.js 已更新
) else (
  echo [注意] 退出码 %RC%：可能有标的抓取失败，已沿用旧数据，详情见上方输出
)
echo.
pause
