; Independent application; preserves both original products and every data root.
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0-preview"
#endif
#ifndef StageDir
  #error "Run scripts\package-ipadhub.ps1 to supply the validated StageDir"
#endif
#define ProjectRoot AddBackslash(SourcePath) + ".."

[Setup]
AppId={{2F7A9C14-E3D0-4D64-8A12-9B59D3CD7EF1}
AppName=iPadHub
AppVersion={#MyAppVersion}
AppVerName=iPadHub {#MyAppVersion}
AppPublisher=iPadHub
DefaultDirName={localappdata}\Programs\iPadHub
DefaultGroupName=iPadHub
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputBaseFilename=iPadHub-{#MyAppVersion}-Setup-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#ProjectRoot}\packaging\iPadHub.ico
LicenseFile={#ProjectRoot}\LICENSE
UninstallDisplayIcon={app}\iPadHub.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
ChangesAssociations=no
ChangesEnvironment=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "{#ProjectRoot}\vendor\ipaddisplay\installer\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建 iPadHub 桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\iPadHub"; Filename: "{app}\iPadHub.exe"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\docs\使用说明.md"
Name: "{autodesktop}\iPadHub"; Filename: "{app}\iPadHub.exe"; WorkingDir: "{app}"; Tasks: desktopicon
