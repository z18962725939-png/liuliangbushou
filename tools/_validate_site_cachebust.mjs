/* 官网「缓存自愈」逻辑验证
 * 直接从生成的 index.html 里抠出真实脚本，在 vm 沙箱里跑四种场景：
 *   1) 页面是旧版(1.2.2) + 线上 1.3.0            → 必须触发重载，且带上 _r 与 _ver=1.3.0
 *   2) 页面已是最新(1.3.0)                        → 绝不能重载（否则页面自己刷自己）
 *   3) 旧页面 + 已经为 1.3.0 重载过一次           → 不能再重载（防死循环）
 *   4) version.json 取不到 / 跨域失败              → 静默跳过，不影响页面
 * 用法： node tools/_validate_site_cachebust.mjs
 */
import fs from 'node:fs';
import vm from 'node:vm';

const HTML = 'installer_output/standalone/index.html';
const html = fs.readFileSync(HTML, 'utf8');

const block = html.match(/\/\* 缓存自愈[\s\S]*?fetch\("version\.json"[\s\S]*?<\/script>/);
if (!block) {
  console.error('✘ 没在产物里找到自愈脚本，构建可能没生效');
  process.exit(1);
}
const code = block[0].replace(/<\/script>\s*$/, '');

function run({ pageVer, latest, href = 'https://example.com/', ok = true, throws = false }) {
  const c = code.replace(/var pageVer="[0-9.]+";/, `var pageVer="${pageVer}";`);
  const reloaded = [];
  const seen = [];
  const ctx = {
    URL: globalThis.URL,
    String, Date, JSON, console,
    location: { href, replace(u) { reloaded.push(u); } },
    fetch(url, opts) {
      seen.push({ url, cache: opts && opts.cache });
      if (throws) return Promise.reject(new Error('network down'));
      return Promise.resolve({ ok, json: () => Promise.resolve(ok ? { version: latest } : {}) });
    },
  };
  vm.createContext(ctx);
  vm.runInContext(c, ctx);
  return new Promise((r) => setTimeout(() => r({ reloaded, seen }), 60));
}

let pass = 0, fail = 0;
function check(name, cond, extra = '') {
  if (cond) { console.log(`  ✔ ${name}`); pass++; }
  else { console.log(`  ✘ ${name} ${extra}`); fail++; }
}

const r1 = await run({ pageVer: '1.2.2', latest: '1.3.0' });
check('场景1 旧页面被缓存 → 触发重载', r1.reloaded.length === 1, `reloaded=${JSON.stringify(r1.reloaded)}`);
check('场景1 重载地址带 _r 时间戳', r1.reloaded[0] && /_r=\d+/.test(r1.reloaded[0]), r1.reloaded[0] || '');
check('场景1 重载地址带 _ver=1.3.0', r1.reloaded[0] && r1.reloaded[0].includes('_ver=1.3.0'), r1.reloaded[0] || '');
check('场景1 fetch 强制 no-store', r1.seen[0] && r1.seen[0].cache === 'no-store');
check('场景1 请求的是相对路径 version.json（保证同源）', r1.seen[0] && r1.seen[0].url === 'version.json', r1.seen[0] && r1.seen[0].url);

const r2 = await run({ pageVer: '1.3.0', latest: '1.3.0' });
check('场景2 页面已最新 → 不重载', r2.reloaded.length === 0);

const r3 = await run({ pageVer: '1.2.2', latest: '1.3.0', href: 'https://example.com/?_ver=1.3.0' });
check('场景3 已为 1.3.0 重载过 → 不再重载（防死循环）', r3.reloaded.length === 0);

const r4 = await run({ pageVer: '1.2.2', latest: '1.3.0', ok: false });
check('场景4 取不到 version.json → 不重载不报错', r4.reloaded.length === 0);

const r5 = await run({ pageVer: '1.2.2', latest: '1.3.0', throws: true });
check('场景5 网络异常 → 静默跳过', r5.reloaded.length === 0);

console.log(`\n结果：${pass} 项通过，${fail} 项失败`);
process.exit(fail ? 1 : 0);
