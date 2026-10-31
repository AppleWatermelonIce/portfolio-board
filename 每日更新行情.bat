@echo off
chcp 936 >nul
cd /d "%~dp0"
echo ============================================================
echo   本地行情/净值更新 —— update_data.py（全历史）
echo   刷新 assets\data.js，双击 index.html 即见最新数据
echo ============================================================
echo.
set "PY=C:\Users\Maple\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "%~dp0logs" mkdir "%~dp0logs"
echo 使用 Python: %PY%
echo.
"%PY%" "%~dp0scripts\update_data.py"
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" goto ok
if "%RC%"=="2" goto degrade
> "%~dp0logs\行情更新失败.md" echo # 行情更新失败  %DATE% %TIME%  退出码 %RC%
echo [失败] 退出码 %RC% —— 详见 logs\行情更新失败.md
goto end
:degrade
> "%~dp0logs\行情更新_降级.md" echo # 行情更新部分降级  %DATE% %TIME%  退出码 2（部分标的抓取失败，已沿用旧数据）
echo [完成/部分降级] 退出码 2 —— 详见 logs\行情更新_降级.md
goto end
:ok
echo [完成] 本地行情/净值已刷新
:end
echo.
rem --task 由计划任务传入，跳过 pause 以免任务挂住
if /i not "%~1"=="--task" pause
exit /b %RC%
