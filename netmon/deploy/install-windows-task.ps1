# Registra o netmon como tarefa agendada que inicia no logon do usuário e
# roda em segundo plano (sem janela) usando pythonw.exe.
# Execute uma vez, no PowerShell, a partir desta pasta:
#   powershell -ExecutionPolicy Bypass -File .\install-windows-task.ps1
# Para remover:
#   Unregister-ScheduledTask -TaskName netmon -Confirm:$false

$ErrorActionPreference = "Stop"
$script = (Resolve-Path (Join-Path $PSScriptRoot "..\netmon.py")).Path
$workdir = Split-Path $script

$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "Python não encontrado no PATH. Instale em https://www.python.org/downloads/ marcando 'Add python.exe to PATH'." }

$action   = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" run --quiet" -WorkingDirectory $workdir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "netmon" -Description "Monitor de qualidade da internet" `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "netmon"
Write-Host "Tarefa 'netmon' registrada e iniciada com $py"
Write-Host "Log: $workdir\netmon.log"
