-- Compiled into McGoverner.app by build_app.sh. Runs the bundled launch.sh
-- (Contents/Resources/launch.sh) in the FOREGROUND, so the app's lifecycle is
-- the server's lifecycle: it stays "open" (normal - that's the running app,
-- not a hang) for as long as the server does, and quitting the app actually
-- stops the server. Backgrounding this with `&` was tried first and was the
-- bug: a background job isn't reliably detached from the app's process group,
-- so the app quitting right after launch could tear the server down with it.
set launcherPath to POSIX path of (path to resource "launch.sh")
with timeout of 999999 seconds
    do shell script "/bin/bash " & quoted form of launcherPath & " > /tmp/mcgoverner-launcher.log 2>&1"
end timeout
