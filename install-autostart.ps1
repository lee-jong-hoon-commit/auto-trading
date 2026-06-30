# Auto Trader - Windows 로그인 시 자동 시작 등록
# PowerShell을 [관리자 권한]으로 실행 후 이 스크립트를 실행하세요
# 실행 방법: 이 파일을 우클릭 → PowerShell로 실행

$TaskName = "AutoTrader-KIS-Upbit"
$WslUser  = "lee"
$WslDistro = "Ubuntu"
$Command  = "cd /mnt/c/Users/kim/Desktop/auto-trader && source venv/bin/activate && pkill -f 'python main.py web' 2>/dev/null; nohup python main.py web >> logs/service.log 2>&1 &"

# 기존 태스크 제거
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# 태스크 구성
$Action = New-ScheduledTaskAction `
    -Execute "wsl.exe" `
    -Argument "-d $WslDistro -u $WslUser -- bash -c `"$Command`""

$Trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

# 등록
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Auto Trader (KIS + Upbit) - WSL 백그라운드 서버" `
    -RunLevel Highest

Write-Host ""
Write-Host "✅ 자동 시작 등록 완료!" -ForegroundColor Green
Write-Host "   태스크명: $TaskName"
Write-Host "   다음 Windows 로그인 시부터 자동으로 서버가 시작됩니다."
Write-Host "   대시보드: http://localhost:8000"
Write-Host ""
Write-Host "❌ 자동 시작 해제하려면:" -ForegroundColor Yellow
Write-Host "   Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host ""
pause
