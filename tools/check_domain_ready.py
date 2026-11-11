# -*- coding: utf-8 -*-
"""检查域名注册进度：信息模板实名审核 → 域名是否已注册成功。

用途：
  · 买域名卡在「实名信息模板审核中」时，用这个看什么时候能下单
  · 下单付款后，用这个确认域名是否已真正注册成功

用法：
    python tools/check_domain_ready.py
    python tools/check_domain_ready.py 流量捕手.cn
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = "流量捕手.cn"

# 腾讯云信息模板审核状态 → 人话
AUDIT_TEXT = {
    "InAudit": ("审核中", "还没通过，等注册局出结果，无法下单"),
    "Approved": ("已通过", "✅ 可以下单买域名了"),
    "Success": ("已通过", "✅ 可以下单买域名了"),
    "Rejected": ("未通过", "❌ 被驳回，要回控制台按提示改资料后重新提交"),
    "Fail": ("未通过", "❌ 被驳回，要回控制台按提示改资料后重新提交"),
}


def to_punycode(domain: str) -> str:
    try:
        return domain.encode("idna").decode("ascii")
    except Exception:
        parts = []
        for seg in domain.split("."):
            try:
                parts.append(seg.encode("idna").decode("ascii"))
            except Exception:
                parts.append(seg)
        return ".".join(parts)


def main():
    global TARGET
    if len(sys.argv) > 1:
        TARGET = sys.argv[1]

    from tencentcloud.common import credential
    from tencentcloud.domain.v20180808 import domain_client, models as dm

    cfg = os.path.join(ROOT, "tools", "cos_config.json.bak")
    if not os.path.isfile(cfg):
        cfg = os.path.join(ROOT, "tools", "cos_config.json")
    c = json.load(open(cfg, encoding="utf-8"))
    cred = credential.Credential(c["secret_id"], c["secret_key"])
    cl = domain_client.DomainClient(cred, "ap-guangzhou")

    print("=" * 60)
    print("域名注册进度检查  目标：%s（%s）" % (TARGET, to_punycode(TARGET)))
    print("=" * 60)

    # 1) 信息模板
    print()
    print("【信息模板实名审核】")
    tmpl_ok = False
    try:
        r = cl.DescribeTemplateList(dm.DescribeTemplateListRequest())
        ts = getattr(r, "TemplateSet", None) or []
        if not ts:
            print("   ⚠️  一个模板都没有 —— 需要先去「域名注册 → 信息模板」创建")
        for t in ts:
            s = t._serialize() if hasattr(t, "_serialize") else {}
            st = str(s.get("AuditStatus", ""))
            name, human = AUDIT_TEXT.get(st, (st or "未知", ""))
            print("   模板 %s  状态=%s (%s)" % (s.get("TemplateId", ""), name, human))
            print("        创建 %s | 更新 %s | 默认=%s" % (
                s.get("CreatedOn", ""), s.get("UpdatedOn", ""), s.get("IsDefault", "")))
            if st in ("Approved", "Success"):
                tmpl_ok = True
    except Exception as e:
        print("   查询失败：", str(e)[:160])

    # 2) 域名是否已注册
    print()
    print("【域名是否已注册成功】")
    registered = False
    try:
        r = cl.DescribeDomainNameList(dm.DescribeDomainNameListRequest())
        names = []
        for d in (getattr(r, "DomainSet", None) or []):
            s = d._serialize() if hasattr(d, "_serialize") else {}
            names.append(str(s.get("DomainName", "")))
        if names:
            print("   账号下已有 %d 个域名：%s" % (len(names), ", ".join(names)))
        hit = any(n == TARGET or to_punycode(n) == to_punycode(TARGET) for n in names)
        if hit:
            print("   ✅ %s 已注册成功！" % TARGET)
            registered = True
        else:
            print("   ⏳ %s 还没注册（说明订单还没提交/付款，或实名还没过）" % TARGET)
    except Exception as e:
        print("   查询失败：", str(e)[:160])

    # 3) 域名还能不能买
    print()
    print("【域名是否还能买】")
    try:
        ck = dm.CheckDomainRequest()
        ck.DomainName = TARGET
        ck.Period = "1"
        rr = cl.CheckDomain(ck)
        av = getattr(rr, "Available", None)
        print("   %s" % ("✅ 仍可注册" if av is True else
                        ("❌ 已被别人注册" if av is False else "? %s" % av)))
    except Exception as e:
        print("   查询失败：", str(e)[:160])

    # 结论
    print()
    print("=" * 60)
    if registered:
        print("结论：域名已到手 → 下一步去 EdgeOne 绑定它，然后跑")
        print("      python tools/verify_domain_live.py %s" % TARGET)
    elif tmpl_ok:
        print("结论：模板已通过 → **现在就可以下单**！")
        print("      控制台 → 域名注册 → 域名购物车 → 勾选模板 → 提交订单 → 付款")
    else:
        print("结论：还卡在模板实名审核 → 继续等，无需操作")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
