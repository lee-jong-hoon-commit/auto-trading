@echo off
:: Auto Trader - 서버 정지

wsl.exe -d Ubuntu -u lee -- bash -c ^
  "pkill -f 'python main.py web' && echo [AUTO-TRADER] 서버 정지됨 || echo [AUTO-TRADER] 실행 중인 서버 없음"

echo.
pause
