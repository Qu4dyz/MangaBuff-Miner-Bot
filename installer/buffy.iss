; Buffy installer (Inno Setup 6).
; Build: ISCC /DAppVersion=11.0.0 /DDistDir=<folder with Buffy.exe> installer\buffy.iss

#ifndef AppVersion
  #define AppVersion "11.1.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\Buffy"
#endif

[Setup]
AppId={{6F4B7C2E-9D1A-4E7B-A3C5-B0FF1E5A9D21}
AppName=Buffy
AppVersion={#AppVersion}
AppVerName=Buffy {#AppVersion}
AppPublisher=Qu4dyz
AppPublisherURL=https://github.com/Qu4dyz/MangaBuff-Miner-Bot
AppSupportURL=https://github.com/Qu4dyz/MangaBuff-Miner-Bot/issues
; Per-user install: no admin prompt, updates in place
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Buffy
DisableProgramGroupPage=yes
DisableDirPage=auto
UninstallDisplayIcon={app}\Buffy.exe
UninstallDisplayName=Buffy
SetupIconFile=..\buffy\assets\buffy.ico
WizardStyle=modern
Compression=lzma2/normal
SolidCompression=no
OutputDir=..\dist
OutputBaseFilename=Buffy-Setup-{#AppVersion}
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Buffy
VersionInfoDescription=Buffy - MangaBuff autopilot installer

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Buffy"; Filename: "{app}\Buffy.exe"
Name: "{autodesktop}\Buffy"; Filename: "{app}\Buffy.exe"; Tasks: desktopicon

[Registry]
; Remove the "start with Windows" entry the app may have added
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Buffy"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\Buffy.exe"; Description: "{cm:LaunchProgram,Buffy}"; Flags: nowait postinstall skipifsilent
; Silent install = self-update from inside Buffy: start the new version right away
Filename: "{app}\Buffy.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM Buffy.exe"; Flags: runhidden; RunOnceId: "StopBuffy"
