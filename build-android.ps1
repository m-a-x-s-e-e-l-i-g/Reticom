param(
    [string]$TransportHost = '',
    [int]$TransportPort = 4242,
    [string]$VersionName = '0.1.0',
    [int]$VersionCode = 1
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
if ($TransportHost -and $TransportHost -notmatch '^[A-Za-z0-9.-]+$') { throw 'TransportHost contains unsupported characters.' }
if ($TransportPort -lt 1 -or $TransportPort -gt 65535) { throw 'TransportPort must be 1-65535.' }
if ($VersionName -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$') { throw 'VersionName must be a semantic version without a leading v.' }
if ($VersionCode -lt 1 -or $VersionCode -gt 2100000000) { throw 'VersionCode must be 1-2100000000.' }
$windowsProject = $project.Replace('\', '/')
$linuxProject = (wsl.exe -- wslpath -a $windowsProject).Trim()
if (-not $linuxProject) { throw 'Could not resolve the project path in WSL.' }
wsl.exe -- bash "$linuxProject/android/build-apk.sh" "$TransportHost" "$TransportPort" "$VersionName" "$VersionCode"
if ($LASTEXITCODE -ne 0) { throw "Android build failed with exit code $LASTEXITCODE" }
