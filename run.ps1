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
$uvDir = Join-Path $env:USERPROFILE ".local\bin"
$env:PATH = "$uvDir;$env:PATH"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $installScript = Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" -TimeoutSec 60
    Invoke-Expression $installScript
    $env:PATH = "$uvDir;$env:PATH"
}
$env:UV_HTTP_TIMEOUT = "60"

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

# uv can end up with a stale registry entry for a managed Python whose actual
# binary is gone (antivirus deleting an unrecognized downloaded .exe is common
# on locked-down school/hospital machines, or a prior run got interrupted
# mid-download) - `uv venv` then exits 0 against a python.exe that isn't
# really there, and neither call here was checking its exit code, so that
# failure surfaced three steps later as a confusing "module .venv could not
# be loaded" error instead of a clear one at the actual point of failure.
# Force-reinstall the interpreter and retry once before giving up.
if (-not (Test-Path ".venv")) {
    uv venv .venv --python 3.12 --quiet
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Output "venv creation failed or produced no working interpreter - reinstalling Python 3.12 and retrying once."
        Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
        uv python install 3.12 --reinstall
        uv venv .venv --python 3.12 --quiet
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path ".venv\Scripts\python.exe")) {
            throw "uv venv failed twice in a row (exit $LASTEXITCODE) - .venv\Scripts\python.exe still missing after reinstalling the interpreter."
        }
    }
}

uv pip install --python ".venv\Scripts\python.exe" --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "uv pip install failed (exit $LASTEXITCODE) - see the output above for which package."
}

if (-not $env:DEEPSEEK_API_KEY -and -not $env:OPENROUTER_API_KEY) {
    Write-Output "WARNING: no LLM key set. Add DEEPSEEK_API_KEY or OPENROUTER_API_KEY to .env."
    Write-Output "         Pick the provider with LLM_PROVIDER=deepseek|openrouter (default: whichever key is set)."
}

& ".venv\Scripts\uvicorn.exe" app.main:app --reload --port 8000
