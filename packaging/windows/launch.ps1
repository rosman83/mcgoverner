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

# run.ps1's own output splits across two files: stdout -> $RunLog, stderr ->
# $RunLog.err (see Start-Process below). PowerShell exceptions and most native
# command errors land on stderr, so reading only $RunLog was silently dropping
# the actual fatal error every time and leaving only harmless status lines -
# always read both together.
function Get-CombinedLog {
    $out = if (Test-Path $RunLog) { Get-Content $RunLog -Raw -ErrorAction SilentlyContinue } else { "" }
    $err = if (Test-Path "$RunLog.err") { Get-Content "$RunLog.err" -Raw -ErrorAction SilentlyContinue } else { "" }
    # Get-Content -Raw on a file that exists but is still empty (very likely
    # right after Start-Process creates it, before run.ps1 has written
    # anything) returns $null, not "" - and $null reaching [regex]::Matches
    # below throws ArgumentNullException, which $ErrorActionPreference =
    # 'Stop' turns into the whole launcher crashing over a log that just
    # hadn't been written to yet.
    if (-not $out) { $out = "" }
    if (-not $err) { $err = "" }
    if ($err) { return "$out`n--- errors ---`n$err" }
    return $out
}

# Every failure path used to just... end, with nothing visible for someone who
# doesn't know to go digging in %TEMP% for a log file. Show a real dialog with
# enough of the actual error to screenshot and send.
function Show-Error([string]$Message) {
    Add-Type -AssemblyName System.Windows.Forms
    $detail = ""
    $raw = Get-CombinedLog
    if ($raw) {
        $detail = if ($raw.Length -gt 1500) { $raw.Substring($raw.Length - 1500) } else { $raw }
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

Write-Host "Downloading the latest version..."
if (-not (Fetch-Code)) {
    Write-Host "FAILED to download. Details:"
    Get-Content $RunLog -ErrorAction SilentlyContinue | Write-Host
    Show-Error "Could not download McGoverner. Check your internet connection - a corporate VPN or firewall blocking/intercepting github.com will also cause this."
    exit 1
}
Write-Host "Downloaded."

Set-Location $InstallDir

# Reap a server left running from a launcher that crashed or was killed
# before it reached its own cleanup code (exactly what the Get-CombinedLog
# bug above used to cause) - otherwise port 8000 stays squatted and this
# launch either fails to bind or silently ends up talking to the old, now
# out-of-date server while reporting success. Only touches a process whose
# command line points at this install dir, so it can't kill an unrelated
# app that happens to also be using port 8000.
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" -ErrorAction SilentlyContinue
    if ($owner -and $owner.CommandLine -like "*$InstallDir*") {
        Stop-Process -Id $owner.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Starting the server (first run can take a minute or two while dependencies install)..."
$psArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallDir "run.ps1"))
$proc = Start-Process -FilePath "powershell.exe" -ArgumentList $psArgs -RedirectStandardOutput $RunLog -RedirectStandardError "$RunLog.err" -WindowStyle Hidden -PassThru

# Open the browser once the server actually answers, not on a blind timer -
# uv installing dependencies on a first run can take a while. Bail early if
# the server process has already died instead of polling the full timeout.
#
# Only ever echo run.ps1's curated "PROGRESS: " lines here, never raw log
# output - a self-healing retry or the no-API-key note used to get echoed
# verbatim (since they were literally the log's last line) and read as "you
# need to fix something" when there was nothing to do. The full raw log
# (including those lines) still gets dumped below on an actual failure -
# this only changes what shows during a normal run.
#
# 120s used to be plenty for the nominal path (one uv venv + pip install),
# but run.ps1's junction-failure fallback chain (retry, try a system Python,
# and as a last resort download+silently install a real Python.org build) can
# now legitimately run past that on a slow connection or a machine that hits
# every fallback - a real user saw the log show a fully successful "Uvicorn
# running on http://127.0.0.1:8000" mere moments after this loop gave up and
# killed that exact process. 600s covers the realistic worst case; a machine
# that's actually stuck (network fully blocked, etc.) still times out, just
# later.
$started = $false
$lastProgress = ""
for ($i = 0; $i -lt 600; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8000" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            $started = $true
            break
        }
    } catch {}
    if ($proc.HasExited) { break }
    $progressMatches = [regex]::Matches((Get-CombinedLog), "(?m)^PROGRESS: (.+)$")
    if ($progressMatches.Count -gt 0) {
        $latest = $progressMatches[$progressMatches.Count - 1].Groups[1].Value
        if ($latest -ne $lastProgress) {
            Write-Host "  $latest"
            $lastProgress = $latest
        } elseif ($i % 10 -eq 0 -and $i -gt 0) {
            Write-Host "  ...still working (${i}s)"
        }
    }
    Start-Sleep -Seconds 1
}

if (-not $started) {
    Write-Host "FAILED to start. Full log:"
    Write-Host (Get-CombinedLog)
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

# Opening the browser is best-effort and separate from "did the server
# start" - it used to share a try/catch with the health check above, so a
# browser-open failure (no default handler registered, an odd file-type
# association on a locked-down school machine, etc.) silently kept $started
# false forever, the retry loop timed out, and the script killed a perfectly
# working server while telling the user "McGoverner didn't start". Now a
# failure here just falls through to the manual-link instructions.
#
# `Start-Process "http://..."` (a bare URL) goes through .NET's Process
# abstraction, which several users reported as a no-op: no exception thrown
# (so $opened came back true), but no browser window ever actually appeared -
# a known flaky pattern on Windows when the default browser is a packaged/
# Store app or under stricter default-app policy. `cmd /c start` is the OS's
# own, much more battle-tested mechanism for "open this URL in the default
# browser" - it's what most Windows dev tools shell out to for exactly this
# reason. The empty "" is required: `start`'s first quoted argument is a
# window title, and without it `start` treats the URL itself as the title
# and opens nothing.
$opened = $false
try {
    Start-Process -FilePath "cmd.exe" -ArgumentList '/c start "" "http://localhost:8000"' -WindowStyle Hidden
    $opened = $true
} catch {}

Write-Host ""
if ($opened) {
    Write-Host "Ready. Your browser should have opened to McGoverner."
    Write-Host "If it didn't, go to: http://localhost:8000"
} else {
    Write-Host "======================================================"
    Write-Host "  McGoverner is running, but couldn't open your browser."
    Write-Host "  Open this link yourself:"
    Write-Host ""
    Write-Host "      http://localhost:8000"
    Write-Host ""
    Write-Host "  (copy it into any browser's address bar)"
    Write-Host "======================================================"
}
Write-Host ""
Write-Host "This window must stay open while you use McGoverner - closing it stops the app."
Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
