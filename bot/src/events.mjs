// 事件总线：所有实时推送的唯一数据源。
//
// 设计取舍：
// · 环形缓冲保留最近 N 条事件。浏览器断线重连会带 Last-Event-ID，可以从断点续推，
//   避免「正看着看板，网抖一下，中间三条订单就没了」。
// · 每个订阅者单独 try/catch。一个卡死的客户端绝不能拖垮主流程 ——
//   机器人首要任务是给客户回消息，看板只是旁路，宁可踢掉订阅者。
// · 事件 id 单调递增且只在进程内唯一。重启后从 1 重新计数，客户端拿到比缓冲更小的
//   Last-Event-ID 时走「全量重放」，由前端重新拉 /orders 对齐状态。
// · 不在这里做鉴权、不碰 HTTP。总线只管「谁在听、听什么、推给谁」。

const DEFAULTS = {
  bufferSize: 200,
  maxSubscribers: 50,
  maxTopicsPerSub: 12,
};

export class TooManySubscribersError extends Error {
  constructor(max) {
    super(`实时订阅数已达上限 ${max}`);
    this.name = "TooManySubscribersError";
    this.code = "TOO_MANY_SUBSCRIBERS";
  }
}

export class EventBus {
  constructor({ bufferSize = DEFAULTS.bufferSize, maxSubscribers = DEFAULTS.maxSubscribers } = {}) {
    this.bufferSize = Math.max(10, Number(bufferSize) || DEFAULTS.bufferSize);
    this.maxSubscribers = Math.max(1, Number(maxSubscribers) || DEFAULTS.maxSubscribers);
    this.seq = 0;
    this.buffer = [];
    this.subscribers = new Map(); // subId -> { topics:Set|null, send, close, meta }
    this.nextSubId = 1;
    this.published = 0;
    this.droppedSubscribers = 0;
    this.startedAt = Date.now();
    this._closing = false;
  }

  /**
   * 发布一条事件。永不抛错：看板推送失败不能影响机器人主流程。
   * @param {string} type 事件类型，如 order.created
   * @param {object} data 事件负载（务必先脱敏，别把客户原文/授权码整段推出去）
   * @returns {{id:string,type:string,ts:number,data:object}}
   */
  publish(type, data = {}) {
    const ev = {
      id: String(++this.seq),
      type: String(type || "message"),
      ts: Date.now(),
      data: data && typeof data === "object" ? data : { value: data },
    };
    this.published += 1;

    this.buffer.push(ev);
    if (this.buffer.length > this.bufferSize) {
      this.buffer.splice(0, this.buffer.length - this.bufferSize);
    }

    if (this.subscribers.size === 0) return ev;

    // 序列化一次，推给所有订阅者，避免 N 次 JSON.stringify
    let frame;
    try {
      frame = formatEvent(ev);
    } catch {
      return ev; // 负载里混进了循环引用之类，丢弃这条推送但不影响缓冲
    }

    for (const [subId, sub] of [...this.subscribers]) {
      if (!wantsTopic(sub, ev.type)) continue;
      try {
        sub.send(frame, ev);
      } catch {
        // 写失败（连接已断 / 消费者卡死）→ 直接摘掉，绝不重试拖时间
        this._detach(subId);
        this.droppedSubscribers += 1;
      }
    }
    return ev;
  }

  /**
   * 发布「瞬时事件」：只推给在线订阅者，不进缓冲、不占事件 id。
   * 用于在线人数这类状态量 —— 它没有回放价值，混进缓冲会把真实业务事件挤掉。
   */
  publishTransient(type, data = {}) {
    if (this.subscribers.size === 0) return;
    let frame;
    try {
      frame = `event: ${type}\ndata: ${JSON.stringify({ type, ts: Date.now(), data })}\n\n`;
    } catch {
      return;
    }
    for (const [subId, sub] of [...this.subscribers]) {
      if (!wantsTopic(sub, String(type))) continue;
      try {
        sub.send(frame, { type, ts: Date.now(), data });
      } catch {
        this._detach(subId);
        this.droppedSubscribers += 1;
      }
    }
  }

  /**
   * 订阅事件。
   * @param {{send:(frame:string,ev:object)=>void, close?:()=>void, topics?:string[]|null, meta?:object}} opts
   * @returns {{id:number, topics:string[]|null, unsubscribe:()=>void}}
   */
  subscribe({ send, close, topics = null, meta = {}, silent = false } = {}) {
    if (typeof send !== "function") throw new TypeError("subscribe 需要一个 send 函数");
    if (this.subscribers.size >= this.maxSubscribers) {
      throw new TooManySubscribersError(this.maxSubscribers);
    }
    const id = this.nextSubId++;
    const topicSet = normalizeTopics(topics);
    this.subscribers.set(id, { topics: topicSet, send, close, meta, connectedAt: Date.now() });
    // silent：SSE 建流时要先发 ready 帧，presence 广播必须让路，否则前端拿到的事件顺序是乱的
    if (!silent) this._notifyPresence();
    return {
      id,
      topics: topicSet ? [...topicSet] : null,
      unsubscribe: () => this._detach(id),
    };
  }

  /** 在线人数变化：只广播状态，不进事件缓冲 */
  _notifyPresence() {
    if (this._closing) return;
    try {
      this.publishTransient("presence.changed", { subscribers: this.subscribers.size });
    } catch {
      /* 状态广播失败不影响主流程 */
    }
  }

  _detach(subId) {
    const sub = this.subscribers.get(subId);
    if (!sub) return;
    this.subscribers.delete(subId);
    try {
      sub.close?.();
    } catch {
      /* 关闭回调自身出错无所谓，连接已经不在表里了 */
    }
    this._notifyPresence();
  }

  /** 取 lastEventId 之后的所有缓冲事件；id 非法或早于缓冲区起点时返回全部缓冲 */
  replaySince(lastEventId) {
    const n = Number(lastEventId);
    if (!Number.isFinite(n) || n <= 0) return [];
    const oldest = this.buffer.length ? Number(this.buffer[0].id) : 0;
    // 客户端要的事件已经被挤掉了：给全量缓冲，让它自己跟 /orders 对齐
    if (oldest && n < oldest - 1) return [...this.buffer];
    return this.buffer.filter((ev) => Number(ev.id) > n);
  }

  stats() {
    const byType = {};
    for (const ev of this.buffer) byType[ev.type] = (byType[ev.type] || 0) + 1;
    return {
      subscribers: this.subscribers.size,
      maxSubscribers: this.maxSubscribers,
      published: this.published,
      buffered: this.buffer.length,
      bufferSize: this.bufferSize,
      droppedSubscribers: this.droppedSubscribers,
      uptimeSec: Math.round((Date.now() - this.startedAt) / 1000),
      bufferedByType: byType,
    };
  }

  /** 优雅停机用：把所有长连接掐掉，否则 server.close() 会一直等下去 */
  closeAll() {
    this._closing = true;
    for (const subId of [...this.subscribers.keys()]) this._detach(subId);
  }
}

function normalizeTopics(topics) {
  if (!topics) return null;
  const arr = (Array.isArray(topics) ? topics : String(topics).split(","))
    .map((t) => String(t).trim())
    .filter(Boolean)
    .slice(0, DEFAULTS.maxTopicsPerSub);
  return arr.length ? new Set(arr) : null;
}

function wantsTopic(sub, type) {
  if (!sub.topics) return true;
  if (sub.topics.has(type)) return true;
  // 支持 order.* 这种前缀订阅
  const dot = type.indexOf(".");
  if (dot > 0 && sub.topics.has(type.slice(0, dot) + ".*")) return true;
  return false;
}

/** 序列化为 SSE 帧。data 必须单行，所以 JSON 化后去掉换行由 JSON 自身保证安全。 */
export function formatEvent(ev) {
  return `id: ${ev.id}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`;
}

export default EventBus;
