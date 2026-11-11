"""上传短剧剪辑组件包到 COS，并当场验证客户能分块下载。

用法
----
    python tools/publish_drama_bundle.py --src <本地 zip>
    python tools/publish_drama_bundle.py --src <zip> --key trafficcatcher/drama_clip.zip

★ 为什么上传完必须真的发一次 Range 请求
--------------------------------------
组件包 275MB，下载器是靠 Range 分块并发拉的（drama_bundle._fetch_block）。
它有一个明确的判断：服务端返回 200 就说明忽略了分段请求，
直接抛「服务端忽略了分段请求，无法分块下载」然后整包失败。
COS 天生支持 Range，但哪天切到不支持的源，客户点下去就是一次必现失败，
而且失败发生在客户机器上、我们看不见。所以这里每发一次版都真打一次
`bytes=0-1023`，拿不到 206 就直接把本次发布判失败。

★ 为什么不走 CDN 域名
--------------------
cos_config.json 里 cdn_domain 是空的，下载直连 COS。
这个包一年也下不了几次，没必要为它加一层 CDN 成本。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
DEFAULT_KEY = "trafficcatcher/drama_clip.zip"


def make_client(cfg: dict):
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore

    return CosS3Client(
        CosConfig(
            Region=cfg["region"],
            SecretId=cfg["secret_id"],
            SecretKey=cfg["secret_key"],
            Scheme="https",
            # 上行这么慢，默认超时会把还在传的块掐断，给到 120 秒
            Timeout=120,
        )
    )


def _public_url(cfg: dict, key: str) -> str:
    cdn = (cfg.get("cdn_domain") or "").strip()
    base = cdn.rstrip("/") if cdn else (
        f"https://{cfg['bucket']}.cos.{cfg.get('region', 'ap-guangzhou')}.myqcloud.com"
    )
    return f"{base}/{key}"


def verify_remote(url: str, expect_size: int) -> bool:
    ok = True

    # ① HEAD 拿到大小
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            size = int(r.headers.get("Content-Length") or 0)
            print(f"   HEAD  {r.status}　{size:,} 字节")
            if size != expect_size:
                print(f"   [失败] 大小对不上：本地 {expect_size:,}，云端 {size:,}")
                ok = False
    except Exception as e:  # noqa: BLE001
        print(f"   [失败] HEAD 失败：{type(e).__name__}: {e}")
        return False

    # ② Range 必须给 206，否则客户的下载器在第一块就崩
    try:
        req = urllib.request.Request(url)
        req.add_header("Range", "bytes=0-1023")
        with urllib.request.urlopen(req, timeout=60) as r:
            got = len(r.read())
            print(f"   Range {r.status}　取回 {got} 字节")
            if r.status != 206 or got != 1024:
                print(f"   [失败] 分块不可用（status={r.status}, 字节={got}）")
                ok = False
    except Exception as e:  # noqa: BLE001
        print(f"   [失败] Range 失败：{type(e).__name__}: {e}")
        return False

    return ok


def _meta_path(src: Path) -> Path:
    return Path(str(src) + ".upload.json")


def upload_resumable(
    client,
    bucket: str,
    key: str,
    src: Path,
    *,
    part_mb: int = 5,
    threads: int = 2,
) -> None:
    """自己管分块，块级记账 —— 断了下次接着传，只补缺的块。

    ★ 为什么不用 SDK 自带的 upload_file
    ----------------------------------
    本机上行实测只有 0.35MB/s（10MB 传了 29 秒）。upload_file 拿 10MB 一块
    8 并发去抢这点带宽，4 分钟后整批重试耗尽，抛
    "some upload_part fail after max_retry"，而且不告诉你哪几块已经成了 ——
    275MB 得从头再来，再慢 10 分钟。

    这里把 UploadId 和每块的 ETag 记在本地，中途关掉/断网/被掐，
    下次跑同一个命令直接跳过已完成的块，只补剩下的。
    """
    size = src.stat().st_size
    part = part_mb << 20
    meta = _meta_path(src)

    upload_id = ""
    saved: dict[int, str] = {}
    if meta.is_file():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            if (m.get("key") == key and m.get("size") == size
                    and m.get("upload_id")):
                upload_id = m["upload_id"]
                saved = {int(k): v for k, v in (m.get("parts") or {}).items()}
        except (json.JSONDecodeError, OSError, ValueError):
            saved = {}

    if saved:
        print(f"      续传：{len(saved)} 块已完成，接着补剩下的")
    else:
        r = client.create_multipart_upload(
            Bucket=bucket, Key=key, ContentType="application/zip")
        upload_id = r["UploadId"]

    blocks = [(i + 1, s, min(s + part, size))
              for i, s in enumerate(range(0, size, part))]
    pending = [b for b in blocks if b[0] not in saved]
    print(f"      共 {len(blocks)} 块（每块 {part_mb}MB），待传 {len(pending)} 块")

    lock = threading.Lock()

    def _save() -> None:
        try:
            meta.write_text(json.dumps(
                {"key": key, "size": size, "upload_id": upload_id,
                 "parts": {str(k): v for k, v in saved.items()}}),
                encoding="utf-8")
        except OSError:
            pass

    def _one(no: int, start: int, end: int) -> tuple[int, str]:
        with open(src, "rb") as f:
            f.seek(start)
            body = f.read(end - start)
        last = None
        for attempt in range(3):
            try:
                r = client.upload_part(Bucket=bucket, Key=key,
                                       UploadId=upload_id,
                                       PartNumber=no, Body=body)
                return no, r["ETag"]
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(3.0 * (attempt + 1))
        raise RuntimeError(f"第 {no} 块重试 3 次仍失败：{type(last).__name__}: {last}")

    t0 = time.time()
    done = len(saved)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futs = [pool.submit(_one, no, s, e) for no, s, e in pending]
        for fut in as_completed(futs):
            no, etag = fut.result()
            with lock:
                saved[no] = etag
                done += 1
                _save()
                dt = time.time() - t0
                print(f"      {done}/{len(blocks)} 块　{dt:.0f}s", end="\r", flush=True)

    client.complete_multipart_upload(
        Bucket=bucket, Key=key, UploadId=upload_id,
        MultipartUpload={"Part": [{"PartNumber": n, "ETag": saved[n]}
                                  for n, _s, _e in blocks]})
    try:
        meta.unlink()
    except OSError:
        pass
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--key", default=DEFAULT_KEY)
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_file():
        raise SystemExit(f"文件不存在：{src}")
    cfg = json.loads((TOOLS / "cos_config.json").read_text(encoding="utf-8"))

    size = src.stat().st_size
    print(f"上传 {src.name}（{size:,} 字节 → {size / 1048576:.1f} MB）")
    print(f"目标 {args.key}")

    client = make_client(cfg)
    t0 = time.time()
    upload_resumable(client, cfg["bucket"], args.key, src,
                     part_mb=5, threads=2)
    print(f"   上传完成，用时 {time.time() - t0:.0f}s")

    url = _public_url(cfg, args.key)
    print(f"\n验证 {url}")
    if not verify_remote(url, size):
        print("\n[发布失败] 云端校验不过，别把这个地址写进代码。")
        return 1

    print(f"\n[OK] 组件包可用：{url}")
    print(f"     大小 {size:,} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
