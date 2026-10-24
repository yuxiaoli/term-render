#ifndef BinaryPath
  #error BinaryPath must point to the frozen render.exe
#endif
#ifndef PackageVersion
  #error PackageVersion is required
#endif

[Setup]
AppId={{B0B43C24-DF91-41F2-8BB7-9863FA8678C7}
AppName=term-render
AppVersion={#PackageVersion}
AppPublisher=term-render contributors
AppPublisherURL=https://github.com/yuxiaoli/term-render
AppSupportURL=https://github.com/yuxiaoli/term-render/issues
DefaultDirName={localappdata}\Programs\term-render
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputBaseFilename=term-render-{#PackageVersion}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
UninstallDisplayIcon={app}\render.exe

[Files]
Source: "{#BinaryPath}"; DestDir: "{app}"; DestName: "render.exe"; Flags: ignoreversion

Source: "{#SourcePath}\..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Code]
const
  InstallerKey = 'Software\term-render\Installer';

function WinExpandEnvironmentStrings(Source, Destination: String; Size: Cardinal): Cardinal;
  external 'ExpandEnvironmentStringsW@kernel32.dll stdcall';

function NormalizedPath(Value: String): String;
var
  Buffer: String;
  Size: Cardinal;
begin
  Value := Trim(Value);
  if Length(Value) >= 2 then
    if (Value[1] = '"') and (Value[Length(Value)] = '"') then
      Value := Copy(Value, 2, Length(Value) - 2);
  SetLength(Buffer, 32768);
  Size := WinExpandEnvironmentStrings(Value, Buffer, 32768);
  if (Size > 0) and (Size <= 32768) then
    Value := Copy(Buffer, 1, Size - 1);
  StringChangeEx(Value, '/', '\', True);
  Result := RemoveBackslashUnlessRoot(Value);
end;

function SamePath(Left, Right: String): Boolean;
begin
  Result := CompareText(NormalizedPath(Left), NormalizedPath(Right)) = 0;
end;

function PathContains(PathValue, Directory: String): Boolean;
var
  Separator: Integer;
  Entry: String;
begin
  Result := False;
  while True do begin
    Separator := Pos(';', PathValue);
    if Separator = 0 then
      Entry := PathValue
    else
      Entry := Copy(PathValue, 1, Separator - 1);
    if SamePath(Entry, Directory) then begin
      Result := True;
      Exit;
    end;
    if Separator = 0 then
      Exit;
    Delete(PathValue, 1, Separator);
  end;
end;

function WithoutPath(PathValue, Directory: String): String;
var
  Separator, Kept: Integer;
  Entry: String;
begin
  Result := '';
  Kept := 0;
  while True do begin
    Separator := Pos(';', PathValue);
    if Separator = 0 then
      Entry := PathValue
    else
      Entry := Copy(PathValue, 1, Separator - 1);
    if not SamePath(Entry, Directory) then begin
      if Kept > 0 then
        Result := Result + ';';
      Result := Result + Entry;
      Kept := Kept + 1;
    end;
    if Separator = 0 then
      Exit;
    Delete(PathValue, 1, Separator);
  end;
end;

procedure RemoveOwnedPath;
var
  Directory, CurrentPath, UpdatedPath: String;
  OriginallyPresent: Cardinal;
begin
  if not RegQueryStringValue(HKCU, InstallerKey, 'AddedPath', Directory) then
    Exit;
  OriginallyPresent := 1;
  RegQueryDWordValue(HKCU, InstallerKey, 'OriginalPathPresent', OriginallyPresent);
  if RegQueryStringValue(HKCU, 'Environment', 'Path', CurrentPath) then begin
    UpdatedPath := WithoutPath(CurrentPath, Directory);
    if UpdatedPath <> CurrentPath then begin
      if (UpdatedPath = '') and (OriginallyPresent = 0) then begin
        if not RegDeleteValue(HKCU, 'Environment', 'Path') then
          RaiseException('Could not remove the installer-created user PATH value.');
      end else if not RegWriteExpandStringValue(HKCU, 'Environment', 'Path', UpdatedPath) then
        RaiseException('Could not remove term-render from the user PATH.');
    end;
  end;
  RegDeleteValue(HKCU, InstallerKey, 'AddedPath');
  RegDeleteValue(HKCU, InstallerKey, 'OriginalPathPresent');
  RegDeleteKeyIfEmpty(HKCU, InstallerKey);
end;

procedure AddUserPath;
var
  Directory, OwnedDirectory, CurrentPath, UpdatedPath: String;
  OriginallyPresent: Cardinal;
begin
  Directory := ExpandConstant('{app}');
  if RegQueryStringValue(HKCU, InstallerKey, 'AddedPath', OwnedDirectory) then
    if not SamePath(OwnedDirectory, Directory) then
      RemoveOwnedPath;
  OriginallyPresent := 0;
  CurrentPath := '';
  if RegQueryStringValue(HKCU, 'Environment', 'Path', CurrentPath) then
    OriginallyPresent := 1;
  if PathContains(CurrentPath, Directory) then
    Exit;
  UpdatedPath := Directory;
  if CurrentPath <> '' then
    UpdatedPath := CurrentPath + ';' + Directory;
  if not RegWriteStringValue(HKCU, InstallerKey, 'AddedPath', Directory) then
    RaiseException('Could not record the term-render PATH entry.');
  if not RegWriteDWordValue(HKCU, InstallerKey, 'OriginalPathPresent', OriginallyPresent) then begin
    RegDeleteValue(HKCU, InstallerKey, 'AddedPath');
    RaiseException('Could not record the original user PATH state.');
  end;
  if not RegWriteExpandStringValue(HKCU, 'Environment', 'Path', UpdatedPath) then begin
    RegDeleteValue(HKCU, InstallerKey, 'AddedPath');
    RegDeleteValue(HKCU, InstallerKey, 'OriginalPathPresent');
    RegDeleteKeyIfEmpty(HKCU, InstallerKey);
    RaiseException('Could not add term-render to the user PATH.');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    AddUserPath;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveOwnedPath;
end;
