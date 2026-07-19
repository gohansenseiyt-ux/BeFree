; BeFree.iss — Installeur Windows (Inno Setup)
; Installation par utilisateur (pas besoin d'admin) car BeFree ecrit ses
; fichiers de donnees (config.json, stats.json...) a cote de l'exe (voir
; app_paths.py) — un chemin protege type Program Files casserait cette
; ecriture sans elevation systematique.

#define MyAppName "BeFree"
#define MyAppVersion "3.4.2"
#define MyAppPublisher "BeFree"
#define MyAppExeName "BeFree.exe"

[Setup]
AppId={{B3F0A7E2-6A2D-4B8E-9C1F-BEFREE00001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist_installer
OutputBaseFilename=BeFreeSetup
SetupIconFile=icons\befree.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\BeFree\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
