param(
    [string]$Version = '0.1.0'
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$venv = Join-Path $project '.windows-build-venv'
$python = Join-Path $venv 'Scripts\python.exe'

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Version must use MAJOR.MINOR.PATCH without a leading v.'
}

if (-not (Test-Path -LiteralPath $python)) {
    & py -3.11 -c 'import sys' 2>$null
    if ($LASTEXITCODE -eq 0) {
        & py -3.11 -m venv $venv
    } else {
        Write-Output 'Python 3.11 is not installed; using the default compatible Python runtime.'
        & py -m venv $venv
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) {
        throw 'Could not create the isolated Windows build environment.'
    }
}

& $python -m pip install --disable-pip-version-check -r (Join-Path $project 'requirements-windows.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install Windows build dependencies.' }

& $python (Join-Path $project 'scripts\generate_windows_assets.py') $Version
if ($LASTEXITCODE -ne 0) { throw 'Could not generate Windows build metadata.' }

& $python -m PyInstaller --noconfirm --clean (Join-Path $project 'windows\Reticom-Command.spec')
if ($LASTEXITCODE -ne 0) { throw 'Windows executable build failed.' }

$source = Join-Path $project 'dist\Reticom-Command.exe'
$target = Join-Path $project "dist\Reticom-Command-$Version-windows-x64.exe"
Move-Item -Force -LiteralPath $source -Destination $target
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
$checksum = "$hash  $([IO.Path]::GetFileName($target))`n"
[IO.File]::WriteAllText("$target.sha256", $checksum, [Text.Encoding]::ASCII)

Write-Output "EXE: $target"
Write-Output "SHA-256: $hash"
