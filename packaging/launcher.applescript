-- Compiled into Block1Exam.app by build_app.sh. Runs the bundled launch.sh
-- (Contents/Resources/launch.sh) detached, so double-clicking the app doesn't
-- leave a persistent Script Editor/osascript process, then gets out of the way —
-- launch.sh itself opens the browser once the server is actually up.
set launcherPath to POSIX path of (path to resource "launch.sh")
do shell script "nohup /bin/bash " & quoted form of launcherPath & ¬
    " > /tmp/block1exam-launcher.log 2>&1 &"
