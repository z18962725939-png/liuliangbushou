# -*- coding: utf-8 -*-
"""
腾讯云 COS 配置向导 —— 只需粘贴三次，剩下的全自动。

    python tools/setup_cos.py

它会：
    1. 问你要 SecretId / SecretKey / Bucket / Region（密钥输入不回显）
    2. 写进 tools/cos_config.json（该文件已在 .gitignore，不会提交）
    3. 立刻连通验证：密钥对不对、桶存不存在、是不是公有读
    4. 验证通过后问你要不要顺手发布当前安装包

密钥只落在你本机，不上传任何地方。
"""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
CONFIG_FILE = TOOLS / "cos_config.json"

REGIONS = {
    "1": "ap-guangzhou", "2": "ap-shanghai", "3": "ap-beijing",
    "4": "ap-chengdu", "5": "ap-nanjing", "6": "ap-hongkong",
}


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """secret=True 时不回显。两项都支持「回车沿用默认值」。"""
    if default:
        hint = "（已保存，回车沿用）" if secret else f" [{default}]"
    else:
        hint = ""
    while True:
        raw = getpass.getpass(f"{prompt}{hint}：") if secret else input(f"{prompt}{hint}：")
        val = raw.strip()
        if val:
            return val
        if default:
            return default
        print("  这项不能为空，再填一次。")


def main() -> int:
    print("=" * 58)
    print(" 腾讯云 COS 配置向导")
    print("=" * 58)
    print()
    print("还没拿到密钥？两个地址：")
    print("  建桶  https://console.cloud.tencent.com/cos5/bucket")
    print("  密钥  https://console.cloud.tencent.com/cam/capi")
    print("（桶的访问权限要选「公有读私有写」）")
    print()

    # 已有的配置当作默认值：bucket / region 这种建桶时就定死的，不用每次重填
    old: dict = {}
    if CONFIG_FILE.exists():
        try:
            old = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}

    secret_id = ask("SecretId", str(old.get("secret_id") or ""))
    secret_key = ask("SecretKey（输入不显示）", str(old.get("secret_key") or ""), secret=True)
    bucket = ask("Bucket 完整名（含 AppID）", str(old.get("bucket") or ""))

    print("\n地域（桶建在哪个区就选哪个）：")
    for k, v in REGIONS.items():
        print(f"  {k}. {v}")
    cur_region = str(old.get("region") or REGIONS["1"])
    choice = input(f"序号或手填 [{cur_region}]：").strip()
    region = REGIONS.get(choice, choice or cur_region)

    cur_prefix = str(old.get("prefix") or "trafficcatcher")
    prefix = input(f"存放目录前缀 [{cur_prefix}]：").strip() or cur_prefix
    cdn = input(f"CDN 域名（没配就回车跳过）[{old.get('cdn_domain') or ''}]：").strip() \
        or str(old.get("cdn_domain") or "")

    cfg = {
        "secret_id": secret_id,
        "secret_key": secret_key,
        "bucket": bucket,
        "region": region,
        "prefix": prefix.strip("/"),
        "cdn_domain": cdn,
    }

    print("\n正在验证…")

    # ── 1) 密钥 + 桶是否存在 ──
    try:
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore
    except ImportError:
        print("[错误] 缺少 SDK，先装：pip install cos-python-sdk-v5")
        return 1

    try:
        client = CosS3Client(CosConfig(
            Region=region, SecretId=secret_id, SecretKey=secret_key, Scheme="https"))
        client.head_bucket(Bucket=bucket)
        print("  [OK] 密钥正确，桶存在")
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "get_error_code", lambda: "")()
        msg = getattr(exc, "get_error_msg", lambda: str(exc))()
        print(f"  [失败] {code} {msg}")
        print("\n常见原因：")
        print("  · Bucket 名没带 AppID（要 liuliangbushou-1250000000 这种完整名）")
        print("  · Region 选错，和桶实际所在地不一致")
        print("  · 密钥复制时多了空格，或用了子账号但没给 COS 权限")
        return 2

    # ── 2) 是不是公有读（客户能不能下）──
    # 探测方式：匿名 GET 一个不存在的对象，靠错误码区分三种情况——
    #   NoSuchKey    → 匿名读被允许，只是对象不存在 → 公有读 ✅
    #   AccessDenied → 匿名读被拒 → 私有桶 ❌（要去改权限）
    #   NoSuchBucket → 域名压根没命中这个桶（桶名/地域不对，或刚建还没生效）
    # 注意：不能只看 HTTP 404 就判公有读，NoSuchBucket 也是 404。
    import urllib.error
    import urllib.request

    probe = (f"https://{bucket}.cos.{region}.myqcloud.com/"
             f"__probe_{abs(hash(bucket)) % 10**8}__")
    public: bool | None = None
    probe_note = ""
    try:
        urllib.request.urlopen(probe, timeout=15)
        public = True
    except urllib.error.HTTPError as exc:
        body = exc.read(600).decode("utf-8", "ignore")
        if "NoSuchKey" in body:
            public = True
        elif "NoSuchBucket" in body:
            public = None
            probe_note = "NoSuchBucket（域名没命中这个桶）"
        else:
            public = False
            probe_note = f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        public = None
        probe_note = type(exc).__name__

    if public is True:
        print("  [OK] 桶是公有读，客户能下载")
    elif public is False:
        print(f"  [警告] 匿名访问被拒（{probe_note}）→ 桶是私有读，客户会下载失败")
        print("         去控制台 → 该桶 → 权限管理 → 存储桶访问权限 → 改为「公有读私有写」")
    else:
        print(f"  [提示] 公有读未能验证（{probe_note}）")
        print("         新建的桶有时要等几分钟才生效；也可能桶名/地域填得不对。")
        print(f"         探测地址：https://{bucket}.cos.{region}.myqcloud.com/")
        print("         去控制台该桶的「概览」页复制「访问域名」，和上面比一比就知道差在哪。")

    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"\n已写入 {CONFIG_FILE}（已在 .gitignore，不会外泄）")

    if public is not True:
        print("公有读还没确认，先别急着对外发版，免得客户下不动。")

    ans = input("\n立即发布当前安装包？[y/N] ").strip().lower()
    if ans == "y":
        subprocess.run([sys.executable, str(TOOLS / "publish_release.py"), "--set-feed"])
    else:
        print("跳过。之后随时跑：python tools/publish_release.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
