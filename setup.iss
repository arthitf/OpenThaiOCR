; สคริปต์สร้างตัวติดตั้ง OpenThai OCR (Inno Setup 6)
; build.bat จะเรียกไฟล์นี้ให้อัตโนมัติถ้าเครื่องมี Inno Setup

#define AppName "OpenThai OCR"
#define AppVersion "1.4"
#define AppPublisher "arthit.f"
#define AppExe "OpenThaiOCR.exe"

[Setup]
AppId={{8F3C2A41-6B7D-4E2A-9C1F-0A5B7E3D2C91}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=Developed by {#AppPublisher}. OCR model by iApp Technology (Apache-2.0)
DefaultDirName={autopf}\OpenThaiOCR
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; ติดตั้งได้โดยไม่ต้องเป็น admin (ลงเฉพาะผู้ใช้คนนี้) หรือเลือกลงทั้งเครื่องได้
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=OpenThaiOCR_Setup_v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
VersionInfoVersion={#AppVersion}.0.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "สร้างไอคอนบนหน้าจอ (Desktop shortcut)"; GroupDescription: "ทางลัด:"
Name: "contextmenu"; Description: "เพิ่มเมนูคลิกขวา ""อ่านด้วย OpenThai OCR"" ให้ไฟล์ PDF และรูปภาพ"; GroupDescription: "เมนูคลิกขวา:"

[Files]
Source: "dist\OpenThaiOCR\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; ลบไฟล์ของเวอร์ชันเก่าก่อนลงใหม่ กันไฟล์ค้างปนกัน
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\OpenThaiOCR"; ValueType: string; ValueName: ""; ValueData: "อ่านด้วย OpenThai OCR"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\OpenThaiOCR"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\OpenThaiOCR\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.png\shell\OpenThaiOCR"; ValueType: string; ValueName: ""; ValueData: "อ่านด้วย OpenThai OCR"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.png\shell\OpenThaiOCR"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.png\shell\OpenThaiOCR\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.jpg\shell\OpenThaiOCR"; ValueType: string; ValueName: ""; ValueData: "อ่านด้วย OpenThai OCR"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.jpg\shell\OpenThaiOCR"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.jpg\shell\OpenThaiOCR\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.jpeg\shell\OpenThaiOCR"; ValueType: string; ValueName: ""; ValueData: "อ่านด้วย OpenThai OCR"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.jpeg\shell\OpenThaiOCR"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.jpeg\shell\OpenThaiOCR\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.tif\shell\OpenThaiOCR"; ValueType: string; ValueName: ""; ValueData: "อ่านด้วย OpenThai OCR"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.tif\shell\OpenThaiOCR"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.tif\shell\OpenThaiOCR\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.tiff\shell\OpenThaiOCR"; ValueType: string; ValueName: ""; ValueData: "อ่านด้วย OpenThai OCR"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.tiff\shell\OpenThaiOCR"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.tiff\shell\OpenThaiOCR\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu

[Run]
Filename: "{app}\{#AppExe}"; Description: "เปิด OpenThai OCR"; Flags: nowait postinstall skipifsilent
