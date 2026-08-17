# Run every Monday at 7:00 AM Eastern
$Action = New-ScheduledTaskAction -Execute "python" -Argument "`"$PSScriptRoot\monday_scheduler.py`"" -WorkingDirectory "$PSScriptRoot\.."
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 7:00AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
Register-ScheduledTask -TaskName "ITG-Weekly-ATN-Invoice" -Action $Action -Trigger $Trigger -Settings $Settings -Description "ITG: generate weekly ATN-format payroll PDF every Monday morning"
Write-Host "Task registered: ITG-Weekly-ATN-Invoice (Mondays 7:00 AM)"
