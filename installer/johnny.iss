; Inno Setup script -> dist\JohnnySetup.exe  (build with installer\build_exe.ps1)
; Version is injected at CI build time via /DAppVersion=x.y.z
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Johnny AI Butler"
#define Publisher "DopeKitchen Industries"

[Setup]
AppId={{6C1F3B2E-9A4D-4E7B-B1D2-4A0C5E9F7D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\Johnny
; Allow the user to change the destination folder in the wizard
DisableDirPage=no
UsePreviousAppDir=yes
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
var
  ModelsPage: TInputDirWizardPage;

// Create an extra "Models location" page after the standard directory page.
procedure InitializeWizard();
begin
  ModelsPage := CreateInputDirPage(wpSelectDir,
    'Ollama models location',
    'Where should Johnny store AI model files?',
    'Models can be several gigabytes. Pick a drive with enough free space. '
    + 'Leave blank to use the Ollama default location.',
    True, '');
  ModelsPage.Add('Models folder (leave blank for Ollama default):');
  // Pre-fill with the user''s previous choice (stored in registry) if available.
  ModelsPage.Values[0] := GetPreviousData('ModelsDir', '');
end;

// Persist the models-dir choice across upgrades.
procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'ModelsDir', ModelsPage.Values[0]);
end;

// Write install_meta.json so the app knows where everything lives.
procedure WriteInstallMeta();
var
  MetaFile: String;
  ModelsDir: String;
  Json: String;
begin
  MetaFile := ExpandConstant('{app}\install_meta.json');
  ModelsDir := ModelsPage.Values[0];
  // Basic JSON escaping for backslashes in Windows paths
  StringChangeEx(ModelsDir, '\', '\\', True);
  StringChangeEx(ExpandConstant('{app}'), '\', '\\', True);
  Json := '{"install_dir": "' + StringReplace(ExpandConstant('{app}'), '\', '\\', [rfReplaceAll]) + '", '
        + '"models_dir": "' + ModelsDir + '"}';
  SaveStringToFile(MetaFile, Json, False);
end;

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
  if CurStep = ssPostInstall then
  begin
    WriteInstallMeta();
    if not OllamaInstalled() then
    begin
      if MsgBox('Johnny needs Ollama (free) to run its local AI. Install it now with winget?',
                mbConfirmation, MB_YESNO) = IDYES then
        Exec('winget', 'install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements',
             '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;

[Run]
Filename: "{app}\Johnny.exe"; Description: "Launch Johnny"; Flags: postinstall nowait
