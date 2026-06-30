@echo off
:: Auto Trader - 백그라운드 시작
:: 이 파일을 더블클릭하면 백그라운드에서 서버가 실행됩니다

wsl.exe -d Ubuntu -u lee -- bash -c ^
  "cd /mnt/c/Users/kim/Desktop/auto-trader && source venv/bin/activate && nohup python main.py web >> logs/service.log 2>&1 & echo [AUTO-TRADER] 서버 시작됨 PID=$!"

echo.
echo 서버가 백그라운드에서 시작됐습니다.
echo 대시보드: http://localhost:8000
echo 로그: C:\Users\kim\Desktop\auto-trader\logs\service.log
echo.
pause
