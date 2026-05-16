# Mithril — one-line installer for Windows (PowerShell).
#
#   iwr -useb https://raw.githubusercontent.com/AaronGrillot98/mithril/main/install.ps1 | iex
#
# Installs Mithril into $env:LOCALAPPDATA\Mithril\venv and adds a `mithril.cmd`
# launcher to $env:LOCALAPPDATA\Mithril\bin, which is appended to the user PATH.

$ErrorActionPreference = 'Stop'

$Repo       = if ($env:MITHRIL_REPO) { $env:MITHRIL_REPO } else { 'https://github.com/AaronGrillot98/mithril' }
$Ref        = if ($env:MITHRIL_REF)  { $env:MITHRIL_REF }  else { 'main' }
$InstallDir = if ($env:MITHRIL_HOME) { $env:MITHRIL_HOME } else { Join-Path $env:LOCALAPPDATA 'Mithril' }
$BinDir     = Join-Path $InstallDir 'bin'
$VenvDir    = Join-Path $InstallDir 'venv'

function Write-Step($msg)  { Write-Host "  > $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  + $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Die($msg)         { Write-Host "  x $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Installing Mithril" -ForegroundColor White

# --- Find a usable Python 3.10+ ----------------------------------------------
$py = $null
foreach ($cand in @('py -3', 'python', 'python3')) {
    try {
        $parts = $cand -split ' '
        $exe   = $parts[0]
        $args  = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
        $ver = & $exe @args -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) {
            $maj, $min = $ver.Split('.') | ForEach-Object { [int]$_ }
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 10)) {
                $py = $cand
                break
            }
        }
    } catch { }
}
if (-not $py) { Die 'Python 3.10+ is required but was not found on PATH.' }
Write-Ok "Using $py (Python $ver)"

# --- Create venv -------------------------------------------------------------
Write-Step "Creating virtual environment at $VenvDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$pyParts = $py -split ' '
$pyExe   = $pyParts[0]
$pyArgs  = if ($pyParts.Length -gt 1) { $pyParts[1..($pyParts.Length - 1)] } else { @() }
& $pyExe @pyArgs -m venv $VenvDir
if ($LASTEXITCODE -ne 0) { Die 'Failed to create virtual environment.' }
Write-Ok "venv ready"

$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPip = Join-Path $VenvDir 'Scripts\pip.exe'

# --- Install mithril from the repo -------------------------------------------
Write-Step "Installing mithril-llm from $Repo@$Ref"
& $VenvPy -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Die 'Failed to upgrade pip.' }
& $VenvPip install --quiet "git+$Repo.git@$Ref"
if ($LASTEXITCODE -ne 0) { Die 'Failed to install Mithril.' }
Write-Ok "package installed"

# --- Drop launcher into BinDir -----------------------------------------------
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$launcher = Join-Path $BinDir 'mithril.cmd'
@"
@echo off
"$VenvDir\Scripts\mithril.exe" %*
"@ | Set-Content -Encoding ASCII -Path $launcher
Write-Ok "launcher installed: $launcher"

# --- Ensure BinDir is on user PATH -------------------------------------------
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$BinDir*") {
    $newPath = if ([string]::IsNullOrEmpty($userPath)) { $BinDir } else { "$userPath;$BinDir" }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Ok "Added $BinDir to your user PATH"
    Write-Warn2 "Open a new terminal for the PATH change to take effect."
} else {
    Write-Ok "$BinDir already on PATH"
}

Write-Host ""
Write-Host "Done." -ForegroundColor White
Write-Host ""
Write-Host "  Start the proxy:    " -NoNewline; Write-Host "mithril serve" -ForegroundColor Cyan
Write-Host "  One-shot scan:      " -NoNewline; Write-Host "mithril scan `"ignore previous instructions`"" -ForegroundColor Cyan
Write-Host "  Dashboard:          " -NoNewline; Write-Host "http://localhost:8080" -ForegroundColor Cyan
Write-Host ""
