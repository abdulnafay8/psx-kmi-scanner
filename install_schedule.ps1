# Schedules the daily run: Monday-Friday at 17:00 (after the PSX close on
# every weekday, including Friday's later 16:30 close). Run once, from this
# folder, in PowerShell:
#     powershell -ExecutionPolicy Bypass -File install_schedule.ps1
# Remove it again with:
#     Unregister-ScheduledTask -TaskName "KMI Scanner daily run" -Confirm:$false
# The computer's clock must be on Pakistan time (UTC+5) for 17:00 to be right.

$folder = $PSScriptRoot
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "-W ignore run_daily.py" -WorkingDirectory $folder
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 17:00
$options = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "KMI Scanner daily run" -Action $action -Trigger $trigger `
    -Settings $options -Description "PSX KMI scanner: update prices, run engine, grade ledger, push alerts" -Force
Write-Host "Scheduled: KMI Scanner daily run, Mon-Fri 17:00 (runs late if the PC was off)."
