// 自动回复规则 + 机器码识别。
//
// 设计原则：机器人只回答「确定、不会因为版本变化而说错」的问题；
// 涉及故障的，统一引导客户提供必要信息并转人工，绝不瞎编原因。

// 机器码：XXXX-XXXX-XXXX-XXXX（16 位十六进制），也可能是 64 位完整指纹
const SHORT_MACHINE = /\b[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}\b/;
const BARE_MACHINE = /\b[0-9A-Fa-f]{16}\b/;
const FULL_FP = /\b[0-9A-Fa-f]{64}\b/;

export function extractMachine(text) {
  if (!text) return null;
  const s = String(text).trim();
  let m = s.match(SHORT_MACHINE) || s.match(FULL_FP);
  if (m) return m[0].toUpperCase();
  m = s.match(BARE_MACHINE);
  if (m) {
    const h = m[0].toUpperCase();
    return `${h.slice(0, 4)}-${h.slice(4, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}`;
  }
  return null;
}

// 客户是不是在说"已经付款了"（用于判断要不要立刻建待审订单）
export function looksPaid(text) {
  const s = String(text || "");
  return /已?付款|已?支付|已转账|付过了|转过了|已付款|已下单|截图/.test(s);
}

export function buildReplies(cfg) {
  const download = cfg.downloadUrl || "（作者还没配置下载页地址）";
  const contact = cfg.adminContact;

  const rules = [
    {
      test: /下载|安装包|安装|链接|地址|怎么装|在哪下/i,
      reply: `软件下载：${download}\n\n下载后一路下一步安装即可，安装完打开软件，左侧「设置 → 授权与激活」里能看到你这台电脑的机器码，把它发我就能出授权码。`,
    },
    {
      test: /多少钱|价格|怎么买|收费|套餐|购买|付费|买一/i,
      reply:
        "目前三档：\n" +
        "· 体验版 19.9 元 / 38 天，每天 10 次调用\n" +
        "· 标准版 29.9 元 / 月，每天 30 次调用\n" +
        "· 专业版 59.9 元 / 月，每天 100 次调用\n\n" +
        "付款后把「付款截图 + 软件里的机器码」一起发我就行。",
    },
    {
      test: /机器码|激活|授权码|怎么填|在哪看|找不到/i,
      reply:
        "机器码在软件里：打开流量捕手 → 左侧「设置」→「授权与激活」，里面有一串形如 A1B2-C3D4-E5F6-7890 的机器码，复制发我就行。\n\n" +
        "授权码发给你后，粘到同一个页面的输入框里点激活即可。",
    },
    {
      test: /到期|过期|续费|续期|延长/i,
      reply: "授权到期后把机器码发我，续费会重新生成一张新码，粘进去激活就能接着用，数据不会丢。",
    },
    {
      test: /换电脑|重装|换机器|新电脑/i,
      reply:
        "授权码绑定机器。换电脑或重装系统后机器码会变，把新机器码发我，我给你重新签发（同一授权期内免费换绑）。",
    },
  ];

  return {
    rules,
    fallback:
      "你好，我已收到你的消息。\n\n" +
      "如果是要购买/续费，把「付款截图 + 机器码」发我就行；\n" +
      "如果是软件问题，简单描述一下现象（比如哪一步、报什么错），我看到会马上处理。\n\n" +
      `也可以直接联系作者：${contact}`,
  };
}

export function matchReply(replies, text) {
  const s = String(text || "");
  for (const r of replies.rules) {
    if (r.test.test(s)) return r.reply;
  }
  return replies.fallback;
}
