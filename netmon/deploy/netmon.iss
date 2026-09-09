; Instalador Windows do netmon (Inno Setup 6). Gerado pelo workflow
; .github/workflows/netmon-windows.yml ou localmente por deploy\build-windows.ps1.
; Instala por usuário (sem administrador), cria atalhos, liga o início
; automático e abre a interface ao final.

#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#define MyAppName "netmon"
#define MyAppExeName "netmon.exe"

[Setup]
AppId={{6E1E0C1B-6C9F-4C1E-9C4B-2B5E3F1D7A21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName=netmon {#MyAppVersion}
AppPublisher=Caio Peret
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=netmon-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayName=netmon (monitor de internet)

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\dist\netmon\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\netmon"; Filename: "{app}\{#MyAppExeName}"; Comment: "Monitor de qualidade da internet"
Name: "{autodesktop}\netmon"; Filename: "{app}\{#MyAppExeName}"; Comment: "Monitor de qualidade da internet"
Name: "{userstartup}\netmon monitor"; Filename: "{app}\{#MyAppExeName}"; Parameters: "run --quiet"; Comment: "Monitor de qualidade da internet (segundo plano)"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install"; Description: "Iniciar o monitor e abrir o netmon"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "stopmonitor"

[InstallDelete]
Type: files; Name: "{userstartup}\netmon monitor.lnk"
