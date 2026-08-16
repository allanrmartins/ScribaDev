; Instalador Windows do ScribaDev (#142 do épico #138).
;
; Decisões (épico #138):
; - Instalação POR USUÁRIO (PrivilegesRequired=lowest): sem UAC, casa com a
;   distribuição sem assinatura de código e com os dados em %LOCALAPPDATA%.
; - Instalador ENXUTO: empacota o dist CPU-first do PyInstaller (sem torch/
;   CUDA/pyannote); as deps pesadas são baixadas sob demanda pelo wizard do app.
; - Upgrade in-place preserva config/notas/índice (%LOCALAPPDATA%\ScribaDev
;   nunca é tocado — nem na DESINSTALAÇÃO; documentado no README).
;
; Compilar: installer\windows\build.ps1 (PyInstaller + ISCC).
; A versão vem por /DAppVersion=... (o build.ps1 lê de scriba/__init__.py).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "dist\scribadev"
#endif

[Setup]
AppId={{5839F5F5-397E-4EFA-A733-644C425F75C0}}
AppName=ScribaDev
AppVersion={#AppVersion}
AppPublisher=Allan Martins
AppPublisherURL=https://github.com/allanrmartins/ScribaDev
AppSupportURL=https://github.com/allanrmartins/ScribaDev/issues
DefaultDirName={userpf}\ScribaDev
DefaultGroupName=ScribaDev
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputBaseFilename=ScribaDev-{#AppVersion}-setup
SetupIconFile={#SourcePath}\..\..\scriba\assets\scriba.ico
UninstallDisplayIcon={app}\ScribaDevApp.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na &Área de Trabalho"; GroupDescription: "Atalhos:"
Name: "autostart"; Description: "&Iniciar o ScribaDev junto com o Windows (bandeja)"; GroupDescription: "Inicialização:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\THIRD-PARTY-LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ScribaDev"; Filename: "{app}\ScribaDevApp.exe"
Name: "{userdesktop}\ScribaDev"; Filename: "{app}\ScribaDevApp.exe"; Tasks: desktopicon
; autostart minimizado (só bandeja), como o autostart do app faz
Name: "{userstartup}\ScribaDev"; Filename: "{app}\ScribaDevApp.exe"; Parameters: "--minimized"; Tasks: autostart

[Run]
Filename: "{app}\ScribaDevApp.exe"; Description: "Abrir o ScribaDev agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; nada: dados do usuário (%LOCALAPPDATA%\ScribaDev) são preservados de propósito
