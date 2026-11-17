; Sifty installer (Inno Setup)
; -----------------------------
; Wraps the standalone dist\sifty.exe in a standard Welcome -> License ->
; Install -> Finish wizard, adds Sifty to PATH so `sifty` works in any
; terminal, and registers a proper entry in Add/Remove Programs.
;
; The version defaults to the value below but the release workflow overrides
; it: ISCC.exe /DAppVersion=X.Y.Z packaging\installer\sifty.iss
;
; Build locally:
;   & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\installer\sifty.iss
; Output lands in dist\sifty-setup.exe (next to the bare exe it wraps).

#ifndef AppVersion
  #define AppVersion "0.9.0"
#endif

#define AppName "Sifty"
#define AppPublisher "Amine Zouaoui"
#define AppURL "https://github.com/Vortrix5/sifty"
#define AppExeName "sifty.exe"

[Setup]
; A stable, unique AppId keeps upgrades/uninstalls tied to the same product.
AppId={{B6A9F3C2-7E4D-4A1B-9C8E-5D2F1A0B3C7D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; Let the user pick "just me" (no UAC) or "all users" (admin) at the start.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Sifty's exe is 64-bit only.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=sifty-setup
SetupIconFile=sifty.ico
UninstallDisplayIcon={app}\{#AppExeName}
WizardStyle=modern
WizardImageFile=wizard-large.bmp
WizardSmallImageFile=wizard-small.bmp
Compression=lzma2/max
SolidCompression=yes
; PATH is changed in [Code]; this lets Windows pick it up without a reboot.
ChangesEnvironment=yes
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addtopath"; Description: "Add Sifty to PATH (so you can run ""sifty"" in any terminal)"; GroupDescription: "Command line:"

[Files]
Source: "..\..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; A clickable Start Menu entry that opens a console ready to run Sifty.
Name: "{group}\{#AppName}"; Filename: "{cmd}"; Parameters: "/K ""{app}\{#AppExeName}"" --help"; IconFilename: "{app}\{#AppExeName}"; Comment: "Open a terminal to run Sifty"
Name: "{group}\{#AppName} on GitHub"; Filename: "{app}\Sifty.url"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[INI]
Filename: "{app}\Sifty.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#AppURL}"

[UninstallDelete]
Type: files; Name: "{app}\Sifty.url"

[Messages]
; Friendly payoff on the final page (it's a CLI, so there's nothing to "launch").
FinishedLabelNoIcons=Sifty has been installed.%n%nOpen a new terminal (PowerShell or Command Prompt) and run "sifty" to get started. Try "sifty doctor" to check your system, or "sifty --help" to see every command.
FinishedLabel=Sifty has been installed.%n%nOpen a new terminal (PowerShell or Command Prompt) and run "sifty" to get started. Try "sifty doctor" to check your system, or "sifty --help" to see every command.

[Code]
{ ---- PATH management ----------------------------------------------------
  Done in code (not [Registry]) so uninstall removes ONLY our entry instead
  of clobbering the whole Path value. Per-machine installs edit the system
  Path under HKLM; per-user installs edit the user Path under HKCU. }

function PathRootKey: Integer;
begin
  if IsAdminInstallMode then
    Result := HKEY_LOCAL_MACHINE
  else
    Result := HKEY_CURRENT_USER;
end;

function PathSubkey: string;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

{ True if Dir is not already a (semicolon-delimited) entry in Path. }
function NeedsAddPath(const Path, Dir: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';') = 0;
end;

procedure EnvAddPath(const Dir: string);
var
  Path: string;
begin
  if not RegQueryStringValue(PathRootKey, PathSubkey, 'Path', Path) then
    Path := '';
  if not NeedsAddPath(Path, Dir) then
    exit;
  if (Path <> '') and (Copy(Path, Length(Path), 1) <> ';') then
    Path := Path + ';';
  Path := Path + Dir;
  RegWriteExpandStringValue(PathRootKey, PathSubkey, 'Path', Path);
end;

procedure EnvRemovePath(const Dir: string);
var
  Path, Needle: string;
  P: Integer;
begin
  if not RegQueryStringValue(PathRootKey, PathSubkey, 'Path', Path) then
    exit;
  Needle := ';' + Uppercase(Dir) + ';';
  P := Pos(Needle, ';' + Uppercase(Path) + ';');
  if P = 0 then
    exit;
  { P is into the ';'-padded copy; subtract the leading ';' we added. }
  Delete(Path, P - 1, Length(Dir) + 1);
  { Tidy up a stray leading/trailing or doubled separator. }
  if (Path <> '') and (Copy(Path, 1, 1) = ';') then
    Delete(Path, 1, 1);
  if (Path <> '') and (Copy(Path, Length(Path), 1) = ';') then
    Delete(Path, Length(Path), 1);
  RegWriteExpandStringValue(PathRootKey, PathSubkey, 'Path', Path);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    EnvAddPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    EnvRemovePath(ExpandConstant('{app}'));
end;
