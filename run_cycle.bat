@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
python -u tools\rotate_logs.py >> logs\rotate.log 2>&1
python -u farm.py >> logs\farm_console.log 2>&1
