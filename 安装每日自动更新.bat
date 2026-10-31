@echo off
chcp 936 >nul
cd /d "%~dp0"
echo ============================================================
echo   安装「本地行情每日自动更新」计划任务
echo   每天 23:40 自动运行 update_data.py，之后无需再手动按
echo ============================================================
echo.
set "TASK=portfolio-board 行情每日更新"
set "RUNNER=%~dp0每日更新行情.bat"

schtasks /create /tn "%TASK%" /tr "%RUNNER% --task" /sc daily /st 23:40 /f
if errorlevel 1 goto fail
echo.
echo [完成] 已创建计划任务：%TASK%（每天 23:40）
echo.
echo 立即试跑一次，把本地数据补齐到最新...
schtasks /run /tn "%TASK%"
echo.
echo 已触发。稍后打开 index.html 即可看到最新数据；
echo 也可在「任务计划程序」中查看该任务的运行结果。
echo.
pause
exit /b 0

:fail
echo.
echo [失败] 创建计划任务失败。请右键本文件 - 以管理员身份运行，再试一次。
echo.
pause
exit /b 1
