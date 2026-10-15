@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   本地估值更新 —— 指数 PE/PB（沪深300 / 标普500 / 纳指100 / 中证A500）
echo   刷新 assets\valuation.js，双击 index.html 即见最新估值底色
echo ============================================================
echo.

set PY=C:\Users\Maple\.workbuddy\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python

"%PY%" "%~dp0scripts\update_valuation.py" %*
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" (
  echo [失败] 退出码 %RC% —— 请看 logs\估值更新失败.md
) else (
  echo [完成] 本地 PE/PB 已刷新
)
echo.
rem --task 由计划任务传入，跳过 pause 以免任务挂住
if /i not "%~1"=="--task" pause
exit /b %RC%
