; Inno Setup script for Viper IDE. Build after PyInstaller:
;   ISCC.exe /DAppVersion=1.0.0 packaging\ViperIDE.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Viper IDE"

[Setup]
AppId={{8C3F4B2A-6E1D-4F7A-9B52-3D7E1A2C9F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Hillyard Tech
AppCopyright=Copyright (c) 2026 Hillyard Tech
DefaultDirName={autopf}\Viper IDE
DefaultGroupName=Viper IDE
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=ViperIDE_Setup_{#AppVersion}
SetupIconFile=viper.ico
UninstallDisplayIcon={app}\ViperIDE.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
CloseApplications=yes
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "contextmenu"; Description: "Add ""Edit with Viper IDE"" to the right-click menu of .py files and folders"; GroupDescription: "Windows integration:"

[Files]
Source: "..\dist\ViperIDE\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Viper IDE"; Filename: "{app}\ViperIDE.exe"
Name: "{autodesktop}\Viper IDE"; Filename: "{app}\ViperIDE.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.py\shell\ViperIDE"; ValueType: string; ValueName: ""; ValueData: "Edit with Viper IDE"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.py\shell\ViperIDE"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\ViperIDE.exe"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.py\shell\ViperIDE\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ViperIDE.exe"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\ViperIDE"; ValueType: string; ValueName: ""; ValueData: "Open Folder in Viper IDE"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\ViperIDE"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\ViperIDE.exe"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\ViperIDE\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ViperIDE.exe"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\Background\shell\ViperIDE"; ValueType: string; ValueName: ""; ValueData: "Open Folder in Viper IDE"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\Background\shell\ViperIDE"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\ViperIDE.exe"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\Background\shell\ViperIDE\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ViperIDE.exe"" ""%V"""; Tasks: contextmenu

[Run]
Filename: "{app}\ViperIDE.exe"; Description: "{cm:LaunchProgram,Viper IDE}"; Flags: nowait postinstall skipifsilent
