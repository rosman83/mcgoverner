# Windows equivalent of run.sh. Lives at the repo root and gets downloaded
# fresh on every launch by packaging/windows/launch.ps1 (mirrors how run.sh
# is fetched fresh for the Mac launcher), so fixes here reach every install
# on their next launch with no relaunch-package rebuild needed.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([^#=\s][^=]*)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
}

New-Item -ItemType Directory -Force -Path "data", "lectures" | Out-Null

# uv bootstraps itself here if missing, and manages its own Python (see below) -
# nobody needs git, Python, or uv pre-installed. Bound the install call so a
# stalled connection (VPN/proxy that hangs instead of rejecting) fails fast
# instead of hanging forever with no error, same reasoning as the Mac side.
#
# "PROGRESS: " lines are a deliberate, curated protocol with launch.ps1 (see
# its heartbeat loop) - it only ever echoes these live, never raw output. A
# self-healing retry or the missing-key note below are not the user's problem
# to react to, so they're plain Write-Output (still visible in the full log
# on a real failure) rather than PROGRESS - showing internal retry mechanics
# or "you need to configure something" during a normal run just reads as
# something being wrong when it isn't.
Write-Output "PROGRESS: Setting up (first run can take a minute or two)..."
$uvDir = Join-Path $env:USERPROFILE ".local\bin"
$env:PATH = "$uvDir;$env:PATH"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $installScript = Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" -TimeoutSec 60
    Invoke-Expression $installScript
    $env:PATH = "$uvDir;$env:PATH"
}
$env:UV_HTTP_TIMEOUT = "60"

# Force uv's Python installs into ProgramData instead of anywhere under the
# user profile, and skip creating the extra `python3.12` PATH shim entirely
# (we always invoke .venv\Scripts\python.exe directly, so it's pure risk with
# no upside). The known cause is OneDrive Files On-Demand sitting in the path
# (its filter driver blocks uv from creating the interpreter's junction,
# failing with "untrusted mount point (os error 448)"), but this has shown up
# in more than one uv-managed location for the same user (AppData\Local,
# ProgramData, and the ~\.local\bin shim all hit it) - something on some
# machines (FSLogix/Citrix profile containers, certain AV/EDR filter drivers -
# common in locked-down school/hospital images) blocks junction creation
# everywhere on disk, not just under one folder. These two settings shrink
# how many junctions uv needs to create; the fallback below is what actually
# recovers on a machine where junction creation is blocked outright.
# https://github.com/astral-sh/uv/issues/19616
$env:UV_PYTHON_INSTALL_DIR = Join-Path $env:ProgramData "McGoverner\uv\python"
$env:UV_DATA_DIR = Join-Path $env:ProgramData "McGoverner\uv"
$env:UV_PYTHON_INSTALL_BIN = "false"
# A previous run (before UV_PYTHON_INSTALL_BIN existed here) may have left a
# python3.12.exe shim in $uvDir, which is on PATH - if it's now a broken
# junction, uv's interpreter discovery trips over it on every run from here
# on, not just the one that created it. Harmless if it never existed.
Remove-Item (Join-Path $uvDir "python3.12.exe") -ErrorAction SilentlyContinue

# Pin the interpreter explicitly instead of trusting whatever's on PATH - same
# reasoning as the Mac fix: the code uses `str | None`-style unions (needs
# 3.10+), and letting uv fall back to a stray old system Python would hit the
# identical "version too low" failure on Windows. `--python 3.12` makes uv use
# its own managed interpreter regardless of what (if anything) is installed.
$minPyMinor = 10
function Test-VenvOk {
    $py = ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { return $false }
    try {
        $minor = & $py -c "import sys; print(sys.version_info[1])"
        return [int]$minor -ge $minPyMinor
    } catch {
        return $false
    }
}
if ((Test-Path ".venv") -and -not (Test-VenvOk)) {
    Write-Output "Existing .venv is on too old a Python - rebuilding it."
    Remove-Item -Recurse -Force ".venv"
}

# Last-resort fallback for a machine where uv can't create ANY junction (see
# above) - a real, already-installed Python found via the launcher/PATH. A
# concrete exe path skips uv's managed-download-and-link step completely: uv
# only needs to create a junction when it resolves a bare version spec like
# "3.12" into something it has to fetch and register itself.
function Find-SystemPython {
    function Try-Python([string]$exe, [string[]]$extraArgs) {
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $null }
        try {
            $checkArgs = $extraArgs + @("-c", "import sys; print(sys.executable if sys.version_info[1] >= $minPyMinor else '')")
            $out = (& $exe @checkArgs 2>$null | Select-Object -Last 1)
            if ($out -and (Test-Path $out.Trim())) { return $out.Trim() }
        } catch { }
        return $null
    }
    foreach ($attempt in @(@{ exe = "py"; args = @("-3.12") }, @{ exe = "py"; args = @("-3") }, @{ exe = "python3"; args = @() }, @{ exe = "python"; args = @() })) {
        $found = Try-Python $attempt.exe $attempt.args
        if ($found) { return $found }
    }
    return $null
}

# If there's no Python anywhere on the machine either (assume zero
# dependencies - plenty of people have genuinely never installed Python),
# fetch and silently install the real python.org build ourselves rather than
# giving up. This is a completely different install mechanism from uv's
# managed Python above (a plain MSI-style installer - no junctions, so it
# isn't exposed to the "untrusted mount point" problem at all), and needs no
# admin rights: InstallAllUsers=0 installs just for the current user.
# ponytail: version is pinned, not queried live - python.org stops
# publishing new Windows installers for a release once it moves to
# security-only maintenance (~18 months after release), so the "latest
# 3.12.x" is actually 3.12.10, several patches behind what uv itself
# downloads (uv's Python comes from a different, still-actively-published
# build). Bump this if 3.12.10 ever disappears from python.org/ftp - check
# https://www.python.org/ftp/python/ for the newest <version>/python-<version>-amd64.exe.
function Install-PythonSilently {
    $pyVersion = "3.12.10"
    $installerUrl = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe"
    $installerPath = Join-Path $env:TEMP "python-$pyVersion-amd64.exe"
    Write-Output "No Python found on this machine - downloading Python $pyVersion (about 25MB)..."
    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -TimeoutSec 120
    } catch {
        Write-Output "Python download failed: $_"
        return $null
    }
    Write-Output "Installing Python $pyVersion (just for your user account - no admin needed)..."
    $proc = Start-Process -FilePath $installerPath `
        -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=1", "Include_test=0" `
        -Wait -PassThru
    Remove-Item $installerPath -ErrorAction SilentlyContinue
    if ($proc.ExitCode -ne 0) {
        Write-Output "Python installer exited with code $($proc.ExitCode)."
        return $null
    }
    $shortVersion = ($pyVersion -split '\.')[0..1] -join ''
    $installedPy = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$shortVersion\python.exe"
    if (Test-Path $installedPy) { return $installedPy }
    return $null
}

# uv can end up with a stale registry entry for a managed Python whose actual
# binary is gone (antivirus deleting an unrecognized downloaded .exe is common
# on locked-down school/hospital machines, or a prior run got interrupted
# mid-download) - `uv venv` then exits 0 against a python.exe that isn't
# really there, and neither call here was checking its exit code, so that
# failure surfaced three steps later as a confusing "module .venv could not
# be loaded" error instead of a clear one at the actual point of failure.
# Force-reinstall the interpreter and retry once before giving up - then, if
# that's still failing, fall back to an existing system Python (see above).
if (-not (Test-Path ".venv")) {
    uv venv .venv --python 3.12 --quiet
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Output "venv creation failed or produced no working interpreter - reinstalling Python 3.12 and retrying once."
        Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
        uv python install 3.12 --reinstall
        uv venv .venv --python 3.12 --quiet
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Output "uv's managed Python still isn't working here - trying an existing system Python install instead."
        Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
        $sysPy = Find-SystemPython
        if (-not $sysPy) { $sysPy = Install-PythonSilently }
        if ($sysPy) { uv venv .venv --python $sysPy --quiet }
        if (-not $sysPy -or $LASTEXITCODE -ne 0 -or -not (Test-Path ".venv\Scripts\python.exe")) {
            throw "uv venv failed - no working Python 3.10+ available (uv's managed install, any existing system Python, and a fresh Python install all failed). See the output above for details."
        }
    }
}

uv pip install --python ".venv\Scripts\python.exe" --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "uv pip install failed (exit $LASTEXITCODE) - see the output above for which package."
}

if (-not $env:DEEPSEEK_API_KEY -and -not $env:OPENROUTER_API_KEY) {
    # Not PROGRESS: the Settings page handles this in-browser now, so there is
    # nothing to act on here - this is a note for someone reading the raw log,
    # not an instruction for the person watching the launcher window.
    Write-Output "Note: no API key set yet - the app's Settings page will walk you through adding one once it opens."
}

Write-Output "PROGRESS: Starting the app..."
# No --reload: that's a dev-only flag that restarts the whole server on any
# .py file change it notices under the watched directory - mid-request, with
# no warning, which reads to a user as a random vague 500. Nobody is
# live-editing source on an end-user machine.
& ".venv\Scripts\uvicorn.exe" app.main:app --port 8000
