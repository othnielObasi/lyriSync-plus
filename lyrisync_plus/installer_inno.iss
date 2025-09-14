; installer_inno.iss — Inno Setup script for LyriSync+
#define MyAppName "LyriSync+"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "Your Team"
#define MyAppExeName "LyriSyncPlus.exe"

[Setup]
AppId={{C61E1D3A-9A0E-4E46-9CF2-0E0B70B9A1AC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={pf}\LyriSyncPlus
DefaultGroupName=LyriSync+
OutputBaseFilename=LyriSyncPlus-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
SetupIconFile=iconLyriSync.ico

[Files]
Source: "dist\main.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "lyrisync_config.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "iconLyriSync.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\iconLyriSync.ico"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\iconLyriSync.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
