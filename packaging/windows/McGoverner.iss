; Inno Setup script -> McGovernerSetup.exe. Wraps the EXISTING launch.ps1/
; run.ps1 pair unchanged - this only changes how those files get onto the
; machine and how the user launches them (real installer wizard, Start Menu
; + Desktop shortcuts, an uninstaller entry) instead of "unzip a folder and
; remember to keep two files together." Auto-update still works exactly like
; before: launch.ps1 re-downloads the app's code from GitHub on every run.
; This installer itself only needs re-running if launch.ps1/run.ps1 change,
; which is rare - see release-windows-launcher.yml.
#define MyAppName "McGoverner"
#define MyAppVersion "1.0"

[Setup]
AppId={{6B2E9C1A-7F4D-4A2E-9C3B-1D8E5F7A2B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
; Installs per-user under LocalAppData, not Program Files - no admin/UAC
; prompt needed, which matters a lot for students on school-managed
; machines who may not have an admin password at all.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=McGovernerSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "launch.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "McGoverner.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Target McGoverner.bat directly (not a hidden PowerShell invocation) so the
; console window stays visible - that visibility is what makes launch
; failures diagnosable at all instead of the app silently closing with
; nothing to show for it.
Name: "{group}\{#MyAppName}"; Filename: "{app}\McGoverner.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\McGoverner.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\McGoverner.bat"; Description: "Launch McGoverner now"; Flags: postinstall nowait skipifsilent shellexec
