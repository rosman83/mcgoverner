# Windows counterpart of packaging/launch.sh. Wrapped into a double-clickable,
# no-visible-window launcher by McGoverner.bat. Job: get the latest code onto
# the machine (no git required - see Fetch-Code), hand off to run.ps1 (which
# owns dependency install and starting the server), then open the browser once
# it actually answers. Re-running this (relaunching the shortcut) is the
# auto-update: it re-downloads the tracked branch every time.
$ErrorActionPreference = 'Stop'

$RepoBranch = "main"
$ZipUrl = "https://github.com/rosman83/mcgoverner/archive/refs/heads/$RepoBranch.zip"
$InstallDir = Join-Path $env:USERPROFILE "McGoverner"
$RunLog = Join-Path $env:TEMP "mcgoverner-run.log"

# Every failure path used to just... end, with nothing visible for someone who
# doesn't know to go digging in %TEMP% for a log file. Show a real dialog with
# enough of the actual error to screenshot and send.
function Show-Error([string]$Message) {
    Add-Type -AssemblyName System.Windows.Forms
    $detail = ""
    if (Test-Path $RunLog) {
        $raw = Get-Content $RunLog -Raw -ErrorAction SilentlyContinue
        if ($raw) {
            $detail = if ($raw.Length -gt 1500) { $raw.Substring($raw.Length - 1500) } else { $raw }
        }
    }
    [System.Windows.Forms.MessageBox]::Show(
        "$Message`n`n$detail",
        "McGoverner failed to start",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

# No git required. Most people have neither git nor a build toolchain
# installed on Windows either - Invoke-WebRequest and Expand-Archive both ship
# with every Windows 10/11 install, so this never depends on anything the user
# would need to set up first.
function Fetch-Code {
    $tmp = Join-Path $env:TEMP ("mcgoverner-dl-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zipPath = Join-Path $tmp "repo.zip"
    try {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $zipPath -TimeoutSec 60 -UseBasicParsing
    } catch {
        "download failed: $_" | Out-File -Append $RunLog
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return $false
    }
    $extractDir = Join-Path $tmp "extracted"
    try {
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    } catch {
        "extract failed: $_" | Out-File -Append $RunLog
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return $false
    }
    $src = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
    if (-not $src) {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return $false
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    # robocopy /MIR mirrors src into InstallDir; excluded dirs/files are left
    # alone on both the copy AND the delete pass, so .env/data/lectures/.venv
    # survive every "update" untouched - same guarantee as the Mac rsync step.
    # Exit codes 0-7 are all "success" for robocopy (checked via $LASTEXITCODE
    # below); 8+ is a real failure. robocopy writes to stderr even on success,
    # which $ErrorActionPreference='Stop' would otherwise turn into a spurious
    # exception, so relax it just for this one call.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    robocopy $src.FullName $InstallDir /MIR /XD data lectures .venv .git /XF .env *>> $RunLog
    $ErrorActionPreference = $prevEAP
    if ($LASTEXITCODE -ge 8) {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return $false
    }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    return $true
}

if (-not (Fetch-Code)) {
    Show-Error "Could not download McGoverner. Check your internet connection - a corporate VPN or firewall blocking/intercepting github.com will also cause this."
    exit 1
}

Set-Location $InstallDir

$psArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallDir "run.ps1"))
$proc = Start-Process -FilePath "powershell.exe" -ArgumentList $psArgs -RedirectStandardOutput $RunLog -RedirectStandardError "$RunLog.err" -WindowStyle Hidden -PassThru

# Open the browser once the server actually answers, not on a blind timer -
# uv installing dependencies on a first run can take a while. Bail early if
# the server process has already died instead of polling the full timeout.
$started = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8000" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            Start-Process "http://localhost:8000"
            $started = $true
            break
        }
    } catch {}
    if ($proc.HasExited) { break }
    Start-Sleep -Seconds 1
}

if (-not $started) {
    Show-Error "McGoverner didn't start. This is usually a dependency that failed to install, or something blocking github.com / astral.sh (corporate VPN or firewall are common causes)."
    # Don't leave a hung install/server running in the background after
    # telling the user it failed.
    if (-not $proc.HasExited) {
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id)" -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    exit 1
}

Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
