; CAD Parts installer (Inno Setup 6). Build with build-release.ps1, which passes /DAppVersion=<version.json>.
; Per-user install: no admin rights, nothing outside the user's own folders.
; The parts folders and profiles (%APPDATA%\CAD Parts) are never touched by install, upgrade or uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B7C1E-4A1D-4E7B-9C51-CAD9A6750001}
AppName=CAD Parts
AppVersion={#AppVersion}
AppVerName=CAD Parts {#AppVersion}
AppPublisher=CAD Parts
; no spaces in the install path: Claude's allowed build command is matched as plain text
DefaultDirName={localappdata}\Programs\CADParts
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=CAD-Parts-Setup-{#AppVersion}
SetupIconFile=cadparts.ico
UninstallDisplayIcon={app}\CAD Parts.exe
UninstallDisplayName=CAD Parts
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; the app window holds this mutex: setup asks to close CAD Parts before updating it
AppMutex=Local\CADPartsWindow
CloseApplications=yes

[Messages]
WelcomeLabel2=This installs CAD Parts {#AppVersion}: describe a part, Claude designs it with build123d, and you see it in 3D.%n%nOn first start CAD Parts checks for the free tools it uses (Claude Code, Git for Windows and a Python CAD engine) and helps install anything missing. You'll need your own Claude account.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "CAD Parts.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "index.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "run.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "cadworker.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "selftest.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "version.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "cadparts.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "Start CAD Parts.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "Stop CAD Parts.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "blender\*"; DestDir: "{app}\blender"; Flags: ignoreversion recursesubdirs
Source: "vendor\*"; DestDir: "{app}\vendor"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\CAD Parts"; Filename: "{app}\CAD Parts.exe"
Name: "{autodesktop}\CAD Parts"; Filename: "{app}\CAD Parts.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CAD Parts.exe"; Description: "Open CAD Parts"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; files the app makes next to itself (the build123d cache and parts live elsewhere)
Type: files; Name: "{app}\mcp-none.json"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\blender\__pycache__"

[Code]
// Stop a running CAD Parts server (and its build worker) so updated files are used and nothing is locked.
procedure StopServer();
var
  Rc: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -WindowStyle Hidden -Command "try { Invoke-RestMethod -Method Post -TimeoutSec 5 http://127.0.0.1:8765/api/quit | Out-Null } catch {}; ' +
    'Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"',
    '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopServer();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopServer();
  if CurUninstallStep = usPostUninstall then
    SuppressibleMsgBox('CAD Parts was removed.' + #13#10 + #13#10 +
      'Your parts folders and profiles were left exactly as they were.', mbInformation, MB_OK, IDOK);
end;
