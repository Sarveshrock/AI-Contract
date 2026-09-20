; Inno Setup script: wraps the PyInstaller one-folder build (dist\ContractLens) into one ContractLens-Setup.exe.
; Build:  ISCC installer\ContractLens.iss     (after: python -m PyInstaller ContractLens.spec --noconfirm)
#define AppName "ContractLens Enterprise"
#define AppVersion "1.0.0"

[Setup]
AppId={{7B2C2D0E-5C34-4F5B-9A61-0C0A17AC0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ContractLens
DefaultDirName={autopf}\ContractLens
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=ContractLens-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\ContractLens.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ContractLens\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ContractLens.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ContractLens.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ContractLens.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

; User settings and data live in %APPDATA%\ContractLens and are deliberately NOT removed on uninstall.
