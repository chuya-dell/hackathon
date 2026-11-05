# Registers (or re-registers) the Windows scheduled task "saihatsu-weekly": every Sunday 21:00.
# Missed runs (PC asleep or off) start when the PC is available again. Runs as the current user, only while logged on.
# Keep this file ASCII only (Windows PowerShell 5.1 misreads UTF-8 without BOM).
#
#   Register : powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_task.ps1
#   Stop     : Disable-ScheduledTask -TaskName saihatsu-weekly
#   Resume   : Enable-ScheduledTask  -TaskName saihatsu-weekly
#   Remove   : Unregister-ScheduledTask -TaskName saihatsu-weekly -Confirm:$false
#   Run now  : Start-ScheduledTask -TaskName saihatsu-weekly
param([string]$At = "21:00")
$repo = Split-Path -Parent $PSScriptRoot
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\scripts\run_weekly.ps1`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "saihatsu-weekly" -Action $action -Trigger $trigger -Settings $settings `
    -Description "saihatsu weekly: every Sunday $At. Stop with: Disable-ScheduledTask -TaskName saihatsu-weekly" -Force | Out-Null
Get-ScheduledTask -TaskName "saihatsu-weekly" | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName "saihatsu-weekly" | Select-Object NextRunTime
