; Inno Setup script for Bytesraw ERP (roadmap M7.2).
;
; Build the app first, then:
;   iscc /DMyAppVersion=0.1.7 packaging\bytesraw-erp.iss
; build.ps1 does both, and reads the version out of constants.py so the two
; can never disagree.
;
; Requires Inno Setup 6.3 or newer, for "x64compatible".

#define MyAppName "Bytesraw ERP"
#define MyAppPublisher "BytesRaw"
#define MyAppURL "https://bytesraw.com"
#define MyAppExeName "BytesrawERP.exe"
; Must match app._APP_USER_MODEL_ID. The app claims this identity at startup
; so the taskbar shows its own icon instead of Python's; if the shortcut does
; not carry the same string, Windows treats the pinned shortcut and the
; running window as two different applications and the pin stops working.
#define MyAppUserModelID "BytesRaw.BytesrawERP"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
; Never change this GUID: it is how Windows recognises an upgrade of an
; existing install rather than a second copy side by side.
AppId={{5A70E093-D5C8-4238-96DF-D1FFFECE6E6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

; Per-machine: a till is shared, and a back-office install should survive the
; technician who set it up moving on. Also what /VERYSILENT deployment wants.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; QtWebEngine 6.11 needs Windows 10 1809 or newer.
MinVersion=10.0.17763

OutputDir=..\dist
OutputBaseFilename=BytesrawERP-{#MyAppVersion}-setup
SetupIconFile=..\src\bytesraw_erp\resources\icon.ico
; LGPL, because the app links Qt through PySide6. Shown on its own wizard
; page, and installed beside the executable so the terms travel with the
; binary rather than only with the source.
LicenseFile=..\LICENSE
WizardStyle=modern

; The payload is ~500 MB of Qt and Chromium, most of it highly compressible.
; Solid LZMA2 is slow to build and worth it: it is the difference between a
; download a client will accept and one they will not.
Compression=lzma2/max
SolidCompression=yes

; Upgrading over a running till is the normal case, not the exception. The
; Restart Manager asks the app to close rather than failing on a locked DLL.
CloseApplications=yes
RestartApplications=no
SetupMutex=BytesrawERPSetupMutex

; Signing is opt-in: build.ps1 defines SignToolName only when it was given a
; -SignCommand, so an unsigned build needs no signtool configuration at all.
; Doing it here rather than signing setup.exe afterwards is deliberate - Inno
; embeds the uninstaller inside the installer, and only this route signs it.
#ifdef SignToolName
SignTool={#SignToolName}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start {#MyAppName} automatically when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; The whole PyInstaller one-folder tree: BytesrawERP.exe beside _internal\.
Source: "..\dist\BytesrawERP\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE.GPL"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelID}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "{#MyAppUserModelID}"
Name: "{autostartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon; AppUserModelID: "{#MyAppUserModelID}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
{ The app writes nothing beside its executable - core/paths.py puts everything
  under %APPDATA% and %LOCALAPPDATA% - so uninstalling the program directory
  leaves a user's accounts, web profiles and Chromium cache behind.

  The cache is documented in paths.py as safe to delete and holds nothing of
  record, so it goes without asking. The roaming data is the user's
  configuration and is only removed if they say so.

  Both resolve for the user running the uninstaller, which under a per-machine
  install is whoever happens to be removing it. Other users' data is left
  alone; there is no reliable way to enumerate it, and guessing at other
  people's profile directories is worse than leaving a few megabytes behind.

  Note that passwords live in the Windows Credential Manager under the service
  name in constants.KEYRING_SERVICE, and are not touched here. }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  CacheDir: String;
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    CacheDir := ExpandConstant('{localappdata}\{#MyAppPublisher}\{#MyAppName}');
    if DirExists(CacheDir) then
      DelTree(CacheDir, True, True, True);

    DataDir := ExpandConstant('{userappdata}\{#MyAppPublisher}\{#MyAppName}');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also remove your saved accounts, settings and signed-in Odoo sessions?'
                + #13#10#13#10 + 'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
