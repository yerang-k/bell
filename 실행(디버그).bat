@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [학교 수업 종소리 알리미 실행 중...]
python app.py
if errorlevel 1 pause
