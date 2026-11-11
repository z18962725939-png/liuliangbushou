// 订单存储。默认内存（进程重启会丢），可选文件持久化或注入平台 KV。
//
// 生产部署建议接 KV：内存实现的订单在冷启动后会消失，
// 客户发了机器码却收不到码，体验很差。

import fs from "node:fs";
import path from "node:path";

export class Store {
  constructor({ file = "", kv = null } = {}) {
    this.kv = kv;
    this.file = file;
    this.mem = new Map();
    if (file) this._load();
  }

  _load() {
    try {
      if (fs.existsSync(this.file)) {
        const data = JSON.parse(fs.readFileSync(this.file, "utf8"));
        for (const [k, v] of Object.entries(data)) this.mem.set(k, v);
      }
    } catch {
      // 读不出来就当空库，不能因为历史文件坏了就让机器人起不来
    }
  }

  _persist() {
    if (!this.file) return;
    try {
      const dir = path.dirname(this.file);
      if (dir && !fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(this.file, JSON.stringify(Object.fromEntries(this.mem), null, 2), "utf8");
    } catch {
      // 写盘失败不阻塞主流程（云端通常是只读或受限文件系统）
    }
  }

  async get(key) {
    if (this.kv) {
      const raw = await this.kv.get(key);
      return raw ? JSON.parse(raw) : null;
    }
    return this.mem.get(key) || null;
  }

  async put(key, value) {
    if (this.kv) {
      await this.kv.put(key, JSON.stringify(value));
      return;
    }
    this.mem.set(key, value);
    this._persist();
  }

  async delete(key) {
    if (this.kv) {
      await this.kv.delete(key);
      return;
    }
    this.mem.delete(key);
    this._persist();
  }

  async list(prefix = "") {
    if (this.kv && this.kv.list) {
      const out = await this.kv.list({ prefix });
      return out.keys || [];
    }
    return [...this.mem.keys()].filter((k) => k.startsWith(prefix));
  }
}

export const ORDER_PREFIX = "order:";

export async function saveOrder(store, order) {
  await store.put(ORDER_PREFIX + order.id, order);
}

export async function getOrder(store, id) {
  return await store.get(ORDER_PREFIX + id);
}

/**
 * 最近订单（给实时看板用）。
 * 只取尾部 key 再按 createdAt 排序：KV 的 list 顺序不保证，内存实现则是插入序，
 * 两边都用同一套「先粗取再精排」，避免为了排序把整个库拉一遍。
 */
export async function listRecentOrders(store, { limit = 50, scan = 300 } = {}) {
  const max = Math.max(1, Number(limit) || 50);
  const keys = await store.list(ORDER_PREFIX);
  const tail = keys.slice(-Math.max(max, Number(scan) || 300));
  const items = await Promise.all(tail.map((k) => store.get(k)));
  return items
    .filter(Boolean)
    .sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
    .slice(0, max);
}
