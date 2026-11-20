#define MyAppName "流量捕手"
#define MyAppVersion "1.28.11"
#define MyAppPublisher "流量捕手工作室"
#define MyAppExeName "流量捕手.exe"

[Setup]
AppId={{5BDDBD5C-2236-4B26-83EE-B1BC915CD180}}
AppName=流量捕手
AppVersion={#MyAppVersion}
AppVerName=流量捕手 {#MyAppVersion}
AppPublisher=流量捕手工作室
AppCopyright=Copyright © 2026 流量捕手工作室
DefaultDirName={autopf}\流量捕手
DefaultGroupName=流量捕手
OutputDir=installer_output
OutputBaseFilename=流量捕手_Setup_v{#MyAppVersion}
SetupIconFile=build\app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UsePreviousAppDir=yes
UninstallDisplayName=流量捕手
CreateUninstallRegKey=yes
; 安装第 2 页直接显示"这次更新了什么"，客户不用装完才知道自己装了啥。
; 文件必须是带 UTF-8 编码标记的 .txt（由 tools/build_changelog.py 生成），
; 否则 Inno 按系统编码读，中文会变成一堆问号。
InfoBeforeFile=installer_output\changelog_recent.txt

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "dist\流量捕手\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 更新日志随安装包一起落地，客户随时能翻，不用联网也不用找作者要
Source: "installer_output\更新日志.html"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\流量捕手\流量捕手"; Filename: "{app}\流量捕手.exe"
Name: "{autodesktop}\流量捕手"; Filename: "{app}\流量捕手.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式设置:"; Flags: unchecked

[Run]
; 完成页默认勾选「查看更新日志」：客户一眼看到每个版本改了什么，而不是装完一脸茫然。
; ★ postinstall 本身就是默认勾选的（要默认取消才写 unchecked）—— 这里不能画蛇添足
;   写 checked，[Run] 段没有这个标志，写上去编译直接失败、还不说哪个标志错。
; shellexec = 用默认浏览器打开本地文件，不需要联网。
; skipifsilent = ★ 静默自动更新时绝不能弹浏览器，否则客户每次更新都被强行开个网页。
; 把日志放第一条：完成页若只渲染首个复选框，也必须先把日志露出来。
Filename: "{app}\更新日志.html"; Description: "查看更新日志（每个版本改了什么）"; Flags: postinstall shellexec skipifsilent
Filename: "{app}\流量捕手.exe"; Description: "立即运行 流量捕手"; Flags: nowait postinstall skipifsilent
; 自动更新走静默安装（/SILENT），上面那条会被 skipifsilent 跳过，
; 结果就是"更新完程序没再打开"，客户以为更新没成功。这里补一条：
; 静默安装完成后自动拉起新版本（runascurrentuser 保证不以管理员身份启动）。
Filename: "{app}\流量捕手.exe"; Flags: nowait runascurrentuser; Check: IsSilentInstall

[Code]
function IsSilentInstall(): Boolean;
begin
  Result := WizardSilent();
end;
