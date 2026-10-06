; Inno Setup script -> dist\JohnnySetup.exe  (build with installer\build_exe.ps1)
#define AppName "Johnny AI Butler"
#define AppVersion "1.5.0"
#define Publisher "DopeKitchen Industries"

[Setup]
AppId={{6C1F3B2E-9A4D-4E7B-B1D2-4A0C5E9F7D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\Johnny
UsePreviousAppDir=no
CloseApplications=force
DefaultGroupName=Johnny
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=JohnnySetup
SetupIconFile=..\assets\johnny.ico
UninstallDisplayIcon={app}\Johnny.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardImageFile=..\assets\wizard_large.bmp
WizardSmallImageFile=..\assets\wizard_small.bmp
DisableReadyPage=yes
DisableDirPage=yes
UninstallDisplayName={#AppName}
DisableProgramGroupPage=yes

[Files]
Source: "..\dist\Johnny\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Johnny"; Filename: "{app}\Johnny.exe"
Name: "{autodesktop}\Johnny"; Filename: "{app}\Johnny.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Code]
// Earlier releases were named "Jarvis". Remove that install first (user data in %APPDATA% is kept).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  OldUninst: String;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM Jarvis.exe /IM Johnny.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  OldUninst := ExpandConstant('{localappdata}\Programs\Jarvis\unins000.exe');
  if FileExists(OldUninst) then
    Exec(OldUninst, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

function OllamaInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'))
            or FileExists(ExpandConstant('{commonpf}\Ollama\ollama.exe'))
            or (FileSearch('ollama.exe', GetEnv('PATH')) <> '');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if (CurStep = ssPostInstall) and (not OllamaInstalled()) then
  begin
    if MsgBox('Johnny needs Ollama (free) to run its local AI. Install it now with winget?',
              mbConfirmation, MB_YESNO) = IDYES then
      Exec('winget', 'install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements',
           '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
  end;
end;

[Run]
Filename: "{app}\Johnny.exe"; Description: "Launch Johnny"; Flags: postinstall nowait
