param(
    [string]$TransportHost = '',
    [int]$TransportPort = 4242
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
if ($TransportHost -and $TransportHost -notmatch '^[A-Za-z0-9.-]+$') { throw 'TransportHost contains unsupported characters.' }
if ($TransportPort -lt 1 -or $TransportPort -gt 65535) { throw 'TransportPort must be 1-65535.' }
$windowsProject = $project.Replace('\', '/')
$linuxProject = (wsl.exe -- wslpath -a $windowsProject).Trim()
if (-not $linuxProject) { throw 'Could not resolve the project path in WSL.' }
wsl.exe -- bash "$linuxProject/android/build-apk.sh" "$TransportHost" "$TransportPort"
if ($LASTEXITCODE -ne 0) { throw "Android build failed with exit code $LASTEXITCODE" }
