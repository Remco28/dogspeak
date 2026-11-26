; Dogspeak for Windows. Built by installer/build.py, which passes AppVersion and Bundle.
; Per-user: no administrator prompt, nothing outside this user's profile.
; What it changes, and what undoes it: README, "What the Windows installer changes".

#ifndef AppVersion
  #error Run installer/build.py; it passes /DAppVersion and /DBundle
#endif

[Setup]
; Never change AppId: Windows uses it to recognise updates and the uninstaller.
AppId={{6F2B8E4D-3C1A-4B7E-9D52-8A0C4E1F7B63}
AppName=Dogspeak
AppVersion={#AppVersion}
AppVerName=Dogspeak {#AppVersion}
AppPublisher=Remco28
AppPublisherURL=https://github.com/Remco28/dogspeak
AppSupportURL=https://github.com/Remco28/dogspeak/issues
DefaultDirName={localappdata}\Programs\Dogspeak
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=Dogspeak-{#AppVersion}-Setup
SetupIconFile={#Bundle}\dogspeak.ico
UninstallDisplayIcon={app}\dogspeak.ico
UninstallDisplayName=Dogspeak
LicenseFile={#Bundle}\LICENSE
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
; Dogspeak holds this while it runs; Setup asks you to quit it before changing files.
AppMutex=io.github.Remco28.Dogspeak.Running
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; An update replaces the program completely, so no stale module survives it.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\dogspeak_app"
Type: filesandordirs; Name: "{app}\vendor"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\data"

[Files]
Source: "{#Bundle}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Dogspeak"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m dogspeak_app.qt_app"; WorkingDir: "{app}"; IconFilename: "{app}\dogspeak.ico"; Comment: "Talk freely, get a clear prompt"; AppUserModelID: "io.github.Remco28.Dogspeak"
Name: "{userdesktop}\Dogspeak"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m dogspeak_app.qt_app"; WorkingDir: "{app}"; IconFilename: "{app}\dogspeak.ico"; Comment: "Talk freely, get a clear prompt"; AppUserModelID: "io.github.Remco28.Dogspeak"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m dogspeak_app.qt_app"; WorkingDir: "{app}"; Description: "Start Dogspeak"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Bytecode Python writes while Dogspeak runs; the whole folder is the program's own.
Type: filesandordirs; Name: "{app}"

[Code]
const
  DataFolder = '{localappdata}\Dogspeak';

var
  DeleteData: Boolean;

function InitializeUninstall(): Boolean;
begin
  DeleteData := False;
  if not UninstallSilent() then
    DeleteData := MsgBox('Also delete your Dogspeak drafts, settings and saved API keys?' + #13#10 + #13#10 +
      'Choose No to keep them for a later reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Data: String;
begin
  Data := ExpandConstant(DataFolder);
  if CurUninstallStep = usUninstall then
  begin
    { The program files are still here: let Dogspeak's own Python forget its keys. }
    if DeleteData then
      Exec(ExpandConstant('{app}\python\python.exe'), '-m dogspeak_app.forget_keys',
           ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    { Logs are the program's own; drafts and settings are yours unless you said otherwise. }
    DelTree(Data + '\logs', True, True, True);
    if DeleteData then
      DelTree(Data, True, True, True)
    else
      RemoveDir(Data);  { only if nothing else is in it }
  end;
end;
