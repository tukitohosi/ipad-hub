#ifndef AppVersion
  #error AppVersion must be passed by Build-Installer.ps1
#endif

[Setup]
AppId={{540E4571-24C8-44AD-83BD-D3277CD75F73}
AppName=MouseLink
AppVersion={#AppVersion}
AppPublisher=MouseLink
AppComments=Windows 与 iPad 共用键鼠
DefaultDirName={localappdata}\Programs\MouseLink
DefaultGroupName=MouseLink
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release\{#AppVersion}
OutputBaseFilename=MouseLink-{#AppVersion}-Setup
SetupIconFile=..\open_bridge\assets\mouselink.ico
UninstallDisplayIcon={app}\MouseLink.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\MouseLink.KVM.Desktop
CloseApplications=no
RestartApplications=no
VersionInfoVersion={#AppVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "..\release\{#AppVersion}\MouseLink\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\MouseLink"; Filename: "{app}\MouseLink.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\MouseLink"; Filename: "{app}\MouseLink.exe"; WorkingDir: "{app}"
Name: "{group}\卸载 MouseLink"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\MouseLink.exe"; Description: "打开 MouseLink"; Flags: nowait postinstall skipifsilent

; User settings and firmware backups are outside {app}; never remove them.
