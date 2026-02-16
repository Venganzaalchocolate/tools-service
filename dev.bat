@echo off
call "%~dp0\.venv\Scripts\activate.bat"
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
