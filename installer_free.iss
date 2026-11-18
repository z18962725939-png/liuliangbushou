; ======================================================================
;  流量捕手 免费版 —— Inno Setup 脚本（2026-10-23）
; ======================================================================
;  为什么单独一个 iss，而不是给 installer.iss 加参数：
;    Inno 的 AppId 决定「装的是不是同一个软件」。免费版和完整版是**两个
;    产品**，客户很可能两个都装（免费版下载、完整版采集）。共用 AppId 的
;    结果是后装的把前一个卸掉，客户会以为"我的完整版被降级了"。
;    同理，安装目录、开始菜单、桌面快捷方式、卸载项都必须分开。
;
;  版本号由 tools/release_free.py 在打包前自动改写（和 installer.iss 一样）。
;  手工编译：  ISCC.exe installer_free.iss
; ======================================================================
#define MyAppName "流量捕手免费版"
#define MyAppVersion "1.28.9"
#define MyAppPublisher "流量捕手工作室"
#define MyAppExeName "流量捕手免费版.exe"

[Setup]
; ★ 免费版独立 AppId，必须和完整版的 5BDDBD5C-... 不同，且发布后不许改。
AppId={{A7F3C1D9-4E2B-4F8A-9C56-2D8B7E1A5C93}}
AppName=流量捕手免费版
AppVersion=1.28.9
AppVerName=流量捕手免费版 1.28.9
AppPublisher=流量捕手工作室
AppCopyright=Copyright © 2026 流量捕手工作室
DefaultDirName={autopf}\流量捕手免费版
DefaultGroupName=流量捕手免费版
OutputDir=installer_output
OutputBaseFilename=流量捕手免费版_Setup_v1.28.9
SetupIconFile=build\app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UsePreviousAppDir=yes
UninstallDisplayName=流量捕手免费版
CreateUninstallRegKey=yes
; 安装第 2 页显示"这次更新了什么"。免费版客户也要知道他装的是什么、
; 修了什么 —— 尤其是"下载地址过期会自动重新解析"这类看不见的改动。
InfoBeforeFile=installer_output\changelog_recent.txt

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "dist\流量捕手免费版\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "installer_output\更新日志.html"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\流量捕手免费版\流量捕手免费版"; Filename: "{app}\流量捕手免费版.exe"
Name: "{autodesktop}\流量捕手免费版"; Filename: "{app}\流量捕手免费版.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式设置:"; Flags: unchecked

[Run]
Filename: "{app}\更新日志.html"; Description: "查看更新日志（每个版本改了什么）"; Flags: postinstall shellexec skipifsilent
Filename: "{app}\流量捕手免费版.exe"; Description: "立即运行 流量捕手免费版"; Flags: nowait postinstall skipifsilent
Filename: "{app}\流量捕手免费版.exe"; Flags: nowait runascurrentuser; Check: IsSilentInstall

[Code]
function IsSilentInstall(): Boolean;
begin
  Result := WizardSilent();
end;
