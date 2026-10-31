@echo off
chcp 936 >nul
cd /d "%~dp0"

echo ============================================================
echo   持仓净值曲线 —— 全历史重建
echo   会把最新数据写进 data.js / history，浏览器下次打开自动接续
echo ============================================================
echo.

set RC=1

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 "%~dp0scripts\update_data.py" %*
set RC=%ERRORLEVEL%
goto report

:try_python
python "%~dp0scripts\update_data.py" %*
set RC=%ERRORLEVEL%

:report
echo.
if "%RC%"=="0" goto ok
echo [注意] 退出码 %RC%：可能有标的抓取失败，已沿用旧数据，详情见上方输出
goto end
:ok
echo [完成] data.js 已更新
:end
echo.
pause
