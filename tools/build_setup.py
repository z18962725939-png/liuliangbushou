# -*- coding: utf-8 -*-
"""把 PyInstaller 产物打包成 Windows 安装包（Inno Setup）。

一条龙做的事：
    1. 从 justoneapi_collector/branding.py 读出软件名 / 版本 / 发行商，
       生成 installer\\流量捕手.iss（不会把品牌名写死在脚本里）。
    2. 生成随包分发的「使用说明.txt」。
    3. 调用 ISCC.exe 编译出 流量捕手_Setup_v1.0.0.exe。
    4. 顺手算出 size + sha256，产出 version.json —— 这个文件上传到你的
       服务器（或 GitHub Release），程序里的「自动更新」就能认出新版本。

用法：
    python tools/build_setup.py                 # 生成 .iss 并编译
    python tools/build_setup.py --iss-only      # 只生成 .iss，不编译
    python tools/build_setup.py --url https://xxx.com/流量捕手_Setup_v1.0.0.exe
                                                # 指定下载地址，写进 version.json
    python tools/build_setup.py --notes "1. 新增xxx"   # 写更新说明

前置条件：
    · 先跑 pyinstaller justoneapi_collector.spec --noconfirm
      （产物在 dist\\流量捕手\\）
    · 本机装了 Inno Setup 6（没装会自动下载安装包到 _tools\\ 并提示）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import branding as B  # noqa: E402


# ----------------------------------------------------------------------
#  路径
# ----------------------------------------------------------------------

DIST_DIR = ROOT / "dist" / B.EXE_NAME
ISS_DIR = ROOT / "installer"
ISS_FILE = ISS_DIR / f"{B.APP_NAME}.iss"
OUT_DIR = ROOT / "installer_output"
README_NAME = "使用说明.txt"

INNO_SETUP_URL = (
    "https://github.com/jrsoftware/issrc/releases/download/"
    "is-6_7_3/innosetup-6.7.3.exe"
)


def _iscc_candidates() -> list[Path]:
    """ISCC.exe 的常见位置（环境变量 → PATH → Scoop → Program Files）。"""
    import shutil

    out: list[Path] = []

    env = os.environ.get("ISCC") or ""
    if env:
        out.append(Path(env))

    # Scoop 装的 inno-setup 会往 PATH 里放 iscc（免管理员，最常见）
    which = shutil.which("iscc") or shutil.which("ISCC")
    if which:
        out.append(Path(which))

    # Scoop 的几种根：环境变量 SCOOP、~/scoop、以及常见的盘符根目录
    scoop_roots = [os.environ.get("SCOOP") or "", str(Path.home() / "scoop")]
    for drive in ("C:\\", "D:\\", "E:\\"):
        scoop_roots.append(drive + "Scoop")
    for root in scoop_roots:
        if root:
            out.append(Path(root) / "apps" / "inno-setup" / "current" / "ISCC.exe")

    # 官方安装器（要管理员权限，装到 Program Files）
    for base in (os.environ.get("ProgramFiles(x86)") or "",
                 os.environ.get("ProgramFiles") or "",
                 str(Path.home() / "AppData" / "Local" / "Programs")):
        if not base:
            continue
        for ver in ("Inno Setup 6", "Inno Setup 5"):
            out.append(Path(base) / ver / "ISCC.exe")
    return out


def find_iscc() -> Path | None:
    for p in _iscc_candidates():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


# ----------------------------------------------------------------------
#  使用说明.txt（随包分发）
# ----------------------------------------------------------------------

def _readme_text() -> str:
    contact = B.APP_CONTACT or "（作者未填写联系方式）"
    home = B.APP_HOMEPAGE or "（作者未填写官网）"
    return f"""{B.APP_NAME} v{B.APP_VERSION}
{B.APP_SLOGAN}
{B.APP_COPYRIGHT}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
一、第一次使用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 打开软件，右上角「设置」页填写你的接口 Token，点保存。
2. 每个板块左下角都有「使用指导」，按 1-2-3 步点一遍即可。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
二、试用与授权
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
· 首次安装后有 {getattr(B, 'TRIAL_DAYS', 7)} 天全功能试用，到期后需要激活。
· 设置页里能看到本机的「机器码」，把它发给作者，会换回一个授权码。
· 拿到授权码后粘贴进设置页 → 点「立即激活」，即可长期使用。
· 接口 Token 已经内置在授权码里，激活后不用再手动填 Token。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三、文件都存到哪
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
· 采集的视频 / 直播录制：桌面「{B.DESKTOP_WORK_DIR}」目录
· 软件配置、登录状态、转写引擎：
  %APPDATA%\\{B.DATA_DIR_NAME}
  卸载软件不会删这个目录；重装 / 升级后配置和登录状态都在。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四、常见问题
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Q：采集不到数据？
A：先看设置页的 Token 有没有填对；再看「接口自检」能不能通。

Q：直播录制没有画面 / 时长不动？
A：看录制列表下方的指示灯。「● 正在录制」说明在录；
   如果是手动粘贴的流地址，记得先在列表里选中对应的主播再开始。

Q：转写很慢？
A：设置页可以选引擎。有 N 卡就选 CUDA，没有就选 CPU（小模型够用）。

Q：怎么升级？
A：设置页 → 软件更新 → 立即检查。下载完会提示安装，
   装完自动重新打开，配置和素材都不会丢。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
联系方式：{contact}
官网：{home}
"""


# ----------------------------------------------------------------------
#  .iss 生成
# ----------------------------------------------------------------------

def _chinese_isl() -> Path:
    """简体中文语言包：编译器自带就用自带的，没有就用项目里那份。

    Inno Setup 官方发行版【不】自带中文（翻译是单独分发的），
    所以项目里自己留了一份 installer/languages/ChineseSimplified.isl，
    保证换台机器、换个 Inno 版本也能编出中文安装包。
    """
    iscc = find_iscc()
    if iscc:
        built_in = iscc.parent / "Languages" / "ChineseSimplified.isl"
        try:
            if built_in.is_file():
                return Path("compiler:Languages") / "ChineseSimplified.isl"
        except OSError:
            pass
    local = ISS_DIR / "languages" / "ChineseSimplified.isl"
    if not local.is_file():
        raise FileNotFoundError(f"缺少中文语言包：{local}")
    return local


def _iss_text() -> str:
    exe = f"{B.EXE_NAME}.exe"
    setup_base = f"{B.SETUP_NAME}_v{B.APP_VERSION}"
    zh = _chinese_isl().as_posix()
    # 注意：Inno 里 "#" 是预处理指令，注释只能用 ";"，用错会直接编译失败
    url_line = (
        f"AppPublisherURL={B.APP_HOMEPAGE}\n"
        f"AppSupportURL={B.APP_HOMEPAGE}\n"
        f"AppUpdatesURL={B.APP_HOMEPAGE}"
        if B.APP_HOMEPAGE
        else "; 未填写官网：在 branding.py 里设好 APP_HOMEPAGE 后重新生成"
    )

    return f"""; Inno Setup 脚本 —— 由 tools/build_setup.py 自动生成，请勿手改。
; 要改软件名 / 版本 / 发行商，改 justoneapi_collector/branding.py 后重新运行本脚本。

#define MyAppId      "{B.SETUP_APP_ID}"
#define MyAppName    "{B.APP_NAME}"
#define MyAppExeName "{exe}"
#define MyAppVersion "{B.APP_VERSION}"
#define MyAppPublisher "{B.APP_PUBLISHER}"
#define MyAppCopyright "{B.APP_COPYRIGHT}"
#define MyAppSlogan  "{B.APP_SLOGAN}"

[Setup]
; ★ AppId 千万不要改：改了会被当成另一个软件，装出两套、卸不干净
AppId={{#MyAppId}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppVerName={{#MyAppName}} v{{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
{url_line}
; 默认装到当前用户，不用弹 UAC；用户想装到公共目录可在安装时选
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={{autopf}}\\{{#MyAppName}}
DefaultGroupName={{#MyAppName}}
DisableProgramGroupPage=yes
DisableDirPage=auto
UninstallDisplayName={{#MyAppName}} v{{#MyAppVersion}}
UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
OutputDir={OUT_DIR.as_posix()}
OutputBaseFilename={setup_base}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 自动更新时用来关掉正在运行的旧版本，装完再拉起来
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=yes
AllowNoIcons=yes
AlwaysShowDirOnReadyPage=yes
ShowLanguageDialog=auto

[Languages]
; 只放简体中文：放两种语言的话每次安装都会先弹"选择语言"，多一次点击。
; 编出来就是纯中文向导，不弹语言框。
Name: "chinesesimplified"; MessagesFile: "{zh}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
; 主程序 + _internal 整个目录
Source: "{DIST_DIR.as_posix()}\\{{#MyAppExeName}}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{DIST_DIR.as_posix()}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"
Name: "{{group}}\\{{#MyAppName}} 使用说明"; Filename: "{{app}}\\{README_NAME}"
Name: "{{group}}\\卸载 {{#MyAppName}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "立即启动 {{#MyAppName}}"; Flags: nowait postinstall skipifsilent
Filename: "{{app}}\\{README_NAME}"; Description: "查看使用说明"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; 只删我们自己的东西，绝不碰 %APPDATA% 里的配置和素材
Type: filesandordirs; Name: "{{app}}"

[Code]
// 卸载前问一句：用户数据（配置 / 登录状态 / 转写引擎）要不要一起删。
// 默认【不删】—— 很多人卸载只是为了重装或升版本。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  Answer: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{{userappdata}}\\TrafficCatcher');
    if DirExists(DataDir) then
    begin
      Answer := MsgBox('是否同时删除本机的配置与缓存数据？' + #13#10 + #13#10 +
                       '包括：接口 Token、扫码登录状态、已下载的转写引擎。' + #13#10 +
                       '（桌面上的视频和直播录制不受影响）' + #13#10 + #13#10 +
                       '选「否」则保留，下次重装可以直接用。',
                       mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
      if Answer = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
"""


# ----------------------------------------------------------------------
#  主流程
# ----------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_version_json(setup_path: Path, url: str, notes: str) -> Path:
    """产出给「自动更新」用的 version.json。"""
    data = {
        "version": B.APP_VERSION,
        "published": time.strftime("%Y-%m-%d"),
        "notes": notes or f"{B.APP_NAME} v{B.APP_VERSION}",
        "mandatory": False,
        "min_version": "1.0.0",
        "win": {
            "url": url or f"把这里换成 {setup_path.name} 的真实下载地址",
            "size": setup_path.stat().st_size,
            "sha256": sha256_of(setup_path),
        },
    }
    out = OUT_DIR / "version.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Windows 安装包")
    ap.add_argument("--iss-only", action="store_true", help="只生成 .iss，不编译")
    ap.add_argument("--url", default="", help="安装包的下载地址（写进 version.json）")
    ap.add_argument("--notes", default="", help="更新说明（写进 version.json）")
    args = ap.parse_args()

    if not (DIST_DIR / f"{B.EXE_NAME}.exe").is_file():
        print(f"✗ 找不到 PyInstaller 产物：{DIST_DIR / (B.EXE_NAME + '.exe')}")
        print("  请先执行：pyinstaller justoneapi_collector.spec --noconfirm")
        return 1

    # 1) 使用说明
    (DIST_DIR / README_NAME).write_text(_readme_text(), encoding="utf-8")

    # 2) .iss（UTF-8 BOM：Inno Setup 6 靠它识别中文）
    ISS_DIR.mkdir(parents=True, exist_ok=True)
    ISS_FILE.write_text(_iss_text(), encoding="utf-8-sig")
    print(f"✔ 已生成脚本：{ISS_FILE}")

    if args.iss_only:
        return 0

    # 3) 找 ISCC
    iscc = find_iscc()
    if not iscc:
        tools = ROOT / "_tools"
        tools.mkdir(exist_ok=True)
        local = tools / "innosetup-6.7.3.exe"
        if not local.is_file():
            print("· 本机没装 Inno Setup，正在下载…")
            try:
                urllib.request.urlretrieve(INNO_SETUP_URL, str(local))
            except Exception as e:  # noqa: BLE001
                print(f"✗ 下载失败：{e}")
                print(f"  手动下载安装：{INNO_SETUP_URL}")
                return 2
        print(f"✗ 需要先安装 Inno Setup，安装包已下好：{local}")
        print("  装完后重新执行本脚本即可。")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"· 正在编译（lzma2 压缩，大包会慢一点，请稍候）…")
    r = subprocess.run([str(iscc), str(ISS_FILE)], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    tail = "\n".join((r.stdout or "").splitlines()[-15:])
    if tail.strip():
        print(tail)
    if r.returncode != 0:
        print((r.stderr or "")[-2000:])
        print("✗ 编译失败")
        return 3

    setup_path = OUT_DIR / f"{B.SETUP_NAME}_v{B.APP_VERSION}.exe"
    if not setup_path.is_file():
        print(f"✗ 编译完成但没找到产物：{setup_path}")
        return 3

    size_mb = setup_path.stat().st_size / 1024 / 1024
    print(f"✔ 安装包：{setup_path}  （{size_mb:.1f} MB）")

    vj = write_version_json(setup_path, args.url, args.notes)
    print(f"✔ 更新清单：{vj}")
    print("  把 version.json 里的 url 改成真实下载地址，和安装包一起上传，")
    print("  老用户打开软件就能收到更新提示。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
