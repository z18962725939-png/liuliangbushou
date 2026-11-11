# -*- coding: utf-8 -*-
"""
把官网部署到「腾讯云云函数 SCF + API 网关」，得到一个真正能对外访问的网址。

背景（都是踩过的坑）：
    - COS 默认域名：响应强制带 Content-Disposition: attachment → 浏览器下载文件
    - EdgeOne 默认域名：只是预览链接 → 客户访问 401
    - SCF + API 网关：响应头自己控制，腾讯自家域名国内可访问、微信不拦、免备案

做什么：
    1. 生成自包含单文件 HTML（tools/build_site_bundle.py）
    2. 打成云函数代码包 zip（index.py + site.html）
    3. 创建/更新 SCF 函数
    4. 创建/更新 API 网关服务 + API（开启集成响应）+ 发布到 release 环境
    5. 轮询验证线上：状态码 / Content-Type / 无 attachment / 页面内容

用法：
    python tools/deploy_scf_site.py            # 部署
    python tools/deploy_scf_site.py --dry-run  # 只看会做什么

凭据：默认用 tools/cos_config.json.bak（主账号，有 SCF/APIGW 权限）。
      子账号 cos_config.json 只有 COS 权限，跑不了。
      也可用环境变量 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY 覆盖。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

REGION = "ap-guangzhou"
FUNCTION_NAME = "trafficcatcher-site"
SERVICE_NAME = "trafficcatcher-site"
API_NAME = "index"
ENV_NAME = "release"

SCF_DIR = os.path.join(ROOT, "deploy", "scf_site")
ZIP_PATH = os.path.join(ROOT, "deploy", "scf_site.zip")


def log(msg=""):
    print(msg, flush=True)


def load_credential():
    from tencentcloud.common import credential

    sid = os.environ.get("TENCENTCLOUD_SECRET_ID")
    sk = os.environ.get("TENCENTCLOUD_SECRET_KEY")
    if sid and sk:
        return credential.Credential(sid, sk)

    for p in ("cos_config.json.bak", "cos_config.json"):
        fp = os.path.join(ROOT, "tools", p)
        if os.path.isfile(fp):
            c = json.load(open(fp, encoding="utf-8"))
            if c.get("secret_id") and c.get("secret_key"):
                log("   使用凭据：%s（%s）" % (p, c["secret_id"][:8] + "..."))
                return credential.Credential(c["secret_id"], c["secret_key"])
    raise SystemExit("[错误] 找不到可用密钥（tools/cos_config.json.bak）")


def build_bundle():
    log("[1/5] 生成自包含单文件 HTML")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_site_bundle.py")],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in r.stdout.strip().splitlines():
        log("   " + line)
    if r.returncode != 0:
        log(r.stderr)
        raise SystemExit("[错误] 生成 bundle 失败")
    html = os.path.join(SCF_DIR, "site.html")
    if not os.path.isfile(html):
        raise SystemExit("[错误] 未生成 site.html")
    log("   完成：%.1f KB" % (os.path.getsize(html) / 1024))


def pack_zip():
    log("[2/5] 打包云函数代码")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in ("index.py", "site.html"):
            fp = os.path.join(SCF_DIR, fn)
            if not os.path.isfile(fp):
                raise SystemExit("[错误] 缺少 %s" % fp)
            z.write(fp, fn)
    sz = os.path.getsize(ZIP_PATH)
    log("   %s（%.1f KB）" % (os.path.basename(ZIP_PATH), sz / 1024))
    if sz > 45 * 1024 * 1024:
        raise SystemExit("[错误] 代码包超过 45MB 上限")
    return base64.b64encode(open(ZIP_PATH, "rb").read()).decode("ascii")


def ensure_role(cred, dry=False):
    """SCF 创建函数时会去找服务角色 SCF_QcsRole；账号首次用云函数时它并不存在。
    这里自动补建（幂等），建好后再创建函数就不会报 ResourceNotFound.Role 了。"""
    import json as _j

    from tencentcloud.cam.v20190116 import cam_client, models

    cl = cam_client.CamClient(cred, REGION)
    try:
        req = models.DescribeRoleListRequest()
        req.Page, req.Rp = 1, 100
        names = [x.RoleName for x in (cl.DescribeRoleList(req).List or [])]
    except Exception:
        names = []

    if "SCF_QcsRole" in names:
        log("[3/6] 服务角色 SCF_QcsRole 已存在")
        return

    if dry:
        log("[3/6] dry-run：将创建服务角色 SCF_QcsRole")
        return

    log("[3/6] 创建服务角色 SCF_QcsRole（云函数必需）")
    doc = _j.dumps({"version": "2.0", "statement": [{
        "effect": "allow",
        "action": ["name/sts:AssumeRole"],
        "principal": {"service": ["scf.qcloud.com"]}}]}, ensure_ascii=False)
    req = models.CreateRoleRequest()
    req.RoleName = "SCF_QcsRole"
    req.PolicyDocument = doc
    req.Description = "云函数(SCF)默认服务角色"
    r = cl.CreateRole(req)
    log("   已创建，RoleId = %s" % r.RoleId)


def ensure_function(cred, zip_b64, dry=False):
    from tencentcloud.scf.v20180416 import scf_client, models

    cl = scf_client.ScfClient(cred, REGION)
    exists = False
    try:
        r = cl.GetFunction(models.GetFunctionRequest.from_json_string(
            json.dumps({"FunctionName": FUNCTION_NAME})))
        exists = bool(r and r.FunctionName)
    except Exception:
        exists = False

    if dry:
        log("[3/5] SCF 函数：%s（dry-run，跳过）" % ("已存在，将更新" if exists else "将新建"))
        return

    if exists:
        log("[3/5] 更新已有云函数 %s" % FUNCTION_NAME)
        cl.UpdateFunctionCode(models.UpdateFunctionCodeRequest.from_json_string(json.dumps({
            "FunctionName": FUNCTION_NAME,
            "Handler": "index.main_handler",
            "Code": {"ZipFile": zip_b64},
        })))
        try:
            cl.UpdateFunctionConfiguration(models.UpdateFunctionConfigurationRequest.from_json_string(
                json.dumps({"FunctionName": FUNCTION_NAME, "Timeout": 15, "MemorySize": 128,
                            "Description": "流量捕手官网静态页"})))
        except Exception as e:
            log("   （配置更新跳过：%s）" % str(e)[:80])
    else:
        log("[3/5] 创建云函数 %s（Python3.9 / 128MB / 15s）" % FUNCTION_NAME)
        cl.CreateFunction(models.CreateFunctionRequest.from_json_string(json.dumps({
            "FunctionName": FUNCTION_NAME,
            "Handler": "index.main_handler",
            "Runtime": "Python3.9",
            "Timeout": 15,
            "MemorySize": 128,
            "Code": {"ZipFile": zip_b64},
            "Type": "Event",
            "Description": "流量捕手官网静态页",
            "Namespace": "default",
        })))
    # 等函数变为 Active
    for _ in range(30):
        try:
            r = cl.GetFunction(models.GetFunctionRequest.from_json_string(
                json.dumps({"FunctionName": FUNCTION_NAME})))
            if r.Status == "Active":
                log("   函数状态：Active")
                return
            st = r.Status
        except Exception as e:
            st = str(e)[:40]
        time.sleep(2)
    log("   （未等到 Active，最后状态：%s，继续）" % st)


def ensure_apigw(cred, dry=False):
    """返回 (service_id, outer_domain)"""
    from tencentcloud.apigateway.v20180808 import apigateway_client, models

    cl = apigateway_client.ApigatewayClient(cred, REGION)

    log("[4/5] API 网关服务")
    service_id = None
    r = cl.DescribeServicesStatus(models.DescribeServicesStatusRequest.from_json_string(
        json.dumps({"Limit": 100})))
    for s in (r.Result.ServiceSet or []):
        if s.ServiceName == SERVICE_NAME:
            service_id = s.ServiceId
            log("   复用已有服务 %s" % service_id)
            break

    if service_id is None:
        if dry:
            log("   dry-run：将新建服务 %s" % SERVICE_NAME)
            return None, None
        r = cl.CreateService(models.CreateServiceRequest.from_json_string(json.dumps({
            "ServiceName": SERVICE_NAME,
            "Protocol": "http&https",
            "ServiceDesc": "流量捕手官网",
            "NetTypes": ["OUTER"],
        })))
        service_id = r.ServiceId
        log("   新建服务 %s" % service_id)

    # 找现有 API
    api_id = None
    try:
        r = cl.DescribeApisStatus(models.DescribeApisStatusRequest.from_json_string(
            json.dumps({"ServiceId": service_id, "Limit": 100})))
        for a in (r.Result.ApiIdStatusSet or []):
            if a.ApiName == API_NAME:
                api_id = a.ApiId
                break
    except Exception:
        pass

    api_cfg = {
        "ServiceId": service_id,
        "ApiName": API_NAME,
        "ApiDesc": "官网首页",
        "ApiType": "NORMAL",
        "AuthType": "NONE",
        "Protocol": "HTTP",
        "RequestConfig": {"Path": "/", "Method": "GET"},
        "ServiceType": "SCF",
        "ServiceScfFunctionName": FUNCTION_NAME,
        "ServiceScfFunctionNamespace": "default",
        "ServiceScfFunctionQualifier": "$LATEST",
        "ServiceScfIsIntegratedResponse": True,
        "ServiceTimeout": 15,
        "ResponseType": "HTML",
    }

    if dry:
        log("   dry-run：%s API（集成响应）并发布" % ("将更新" if api_id else "将新建"))
        return service_id, None

    if api_id:
        api_cfg["ApiId"] = api_id
        cl.ModifyApi(models.ModifyApiRequest.from_json_string(json.dumps(api_cfg)))
        log("   更新 API %s" % api_id)
    else:
        r = cl.CreateApi(models.CreateApiRequest.from_json_string(json.dumps(api_cfg)))
        api_id = r.ApiId
        log("   新建 API %s" % api_id)

    cl.ReleaseService(models.ReleaseServiceRequest.from_json_string(json.dumps({
        "ServiceId": service_id,
        "EnvironmentName": ENV_NAME,
        "ReleaseDesc": "publish site",
    })))
    log("   已发布到 %s 环境" % ENV_NAME)

    r = cl.DescribeService(models.DescribeServiceRequest.from_json_string(
        json.dumps({"ServiceId": service_id})))
    domain = getattr(r, "OuterSubDomain", None) or getattr(r, "InnerSubDomain", None)
    log("   公网域名：%s" % domain)
    return service_id, domain


def verify(url, tries=15, gap=8):
    log("[5/5] 线上验证")
    ua_pc = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
    ua_wx = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 MicroMessenger/8.0.40"
    ok = False
    for i in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua_pc})
            with urllib.request.urlopen(req, timeout=25) as r:
                code = r.status
                ct = r.headers.get("Content-Type", "")
                cd = r.headers.get("Content-Disposition", "")
                body = r.read().decode("utf-8", "ignore")
            if code == 200 and "抖音全域内容工作台" in body:
                ok = True
                log("   第 %d 次探测通过（发布已生效）" % i)
                log("   状态码        : %s" % code)
                log("   Content-Type  : %s" % ct)
                log("   Content-Disposition: %s ← 必须为空，否则会被当文件下载" % (cd or "(无)"))
                log("   页面体积      : %.1f KB" % (len(body.encode("utf-8")) / 1024))
                for kw in ("产品矩阵", "TrafficCatcher_Setup_v", "规划中", "GEO"):
                    log("   含「%s」: %s" % (kw, kw in body))
                break
            log("   第 %d 次：状态 %s，内容未就绪，等 %ds" % (i, code, gap))
        except Exception as e:
            log("   第 %d 次：%s，等 %ds" % (i, str(e)[:70], gap))
        time.sleep(gap)

    if not ok:
        log("   [警告] 自动验证未通过，请手动打开确认：%s" % url)
        return False

    # 微信 UA
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua_wx})
        with urllib.request.urlopen(req, timeout=25) as r:
            log("   微信 UA 状态码: %s" % r.status)
    except Exception as e:
        log("   微信 UA 访问异常：%s" % str(e)[:80])
    return True


def main():
    dry = "--dry-run" in sys.argv
    log("=" * 72)
    log("流量捕手官网 → 腾讯云 SCF + API 网关（%s）" % REGION)
    log("=" * 72)

    build_bundle()
    zip_b64 = pack_zip()
    cred = load_credential()
    ensure_function(cred, zip_b64, dry)
    _, domain = ensure_apigw(cred, dry)

    if dry:
        log("\n[dry-run] 未实际部署。")
        return 0

    if not domain:
        log("[错误] 未拿到网关域名")
        return 1

    url = "https://%s/%s/" % (domain, ENV_NAME)
    log("")
    log("=" * 72)
    log("官网公网地址：")
    log("   %s" % url)
    log("=" * 72)

    verify(url)

    # 记下地址，供二维码 / 体检脚本使用
    meta = {"url": url, "domain": domain, "region": REGION,
            "function": FUNCTION_NAME, "env": ENV_NAME,
            "deployed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    fp = os.path.join(ROOT, "tools", "site_url.json")
    json.dump(meta, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("\n地址已写入：%s" % fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
