/**
 * 端到端仿真：在 Node 里模拟浏览器环境跑 live.js
 *   · fetch 走 Node 原生（不受 CORS 限制，故本用例不验证 CORS，只验证解析/合并/persist）
 *   · <script src> JSONP 用 vm.runInThisContext 模拟真实执行 pingzhongdata
 * 用法：node test_live.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

/* ---------------- 模拟浏览器环境 ---------------- */
const els = new Map();
function stubEl(sel) {
  if (els.has(sel)) return els.get(sel);
  const el = {
    _sel: sel, textContent: '', innerHTML: '', disabled: false,
    style: { cssText: '', display: '' }, onclick: null,
    remove() {}, appendChild() {}, after() {},
  };
  els.set(sel, el);
  return el;
}
let renderCalls = 0;
const store = new Map();

global.window = global;
global.navigator = { onLine: true, userAgent: 'node' };
global.localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, v),
  removeItem: k => store.delete(k),
};
global.document = {
  hidden: false,
  head: {
    appendChild(s) {
      // 模拟 <script src>：下载后在本全局执行
      fetch(s.src, { cache: 'no-store' })
        .then(r => r.text())
        .then(txt => { try { vm.runInThisContext(txt); } catch (e) { console.log('    [脚本执行异常]', String(e).slice(0, 90)); } s.onload && s.onload(); })
        .catch(e => s.onerror && s.onerror(e));
    },
  },
  querySelector: sel => stubEl(sel),
  createElement: () => ({ src: '', async: false, remove() {}, set onload(f) { this._l = f; }, get onload() { return this._l; },
                          set onerror(f) { this._e = f; }, get onerror() { return this._e; } }),
};
global.renderAll = () => { renderCalls++; };
global.setInterval = () => 0;            // 关掉 30 分钟轮询
global.addEventListener = () => {};

/* ---------------- 载入 data.js ---------------- */
const before = {};
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'assets', 'data.js'), 'utf8'));
const P = window.PORTFOLIO_DATA;
P.products.forEach(p => {
  before[p.id] = {
    last: p.d[p.d.length - 1], n: p.d.length,
    v0: p.v[0], vN: p.v[p.v.length - 1],
    sig: JSON.stringify([p.d, p.v]),
  };
});
store.clear();                                  // 强制一次全新刷新（忽略 6h 节流）
console.log(`载入 ${P.products.length} 个标的，基座更新于 ${P.updated}`);

/* ---------------- 执行 live.js ---------------- */
const t0 = Date.now();
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'assets', 'live.js'), 'utf8'));

const stat = () => stubEl('#lstat').textContent;
(async () => {
  let last = '';
  for (let i = 0; i < 300; i++) {
    await new Promise(r => setTimeout(r, 1000));
    const s = stat();
    if (s !== last) { process.stdout.write(`\r  [${((Date.now() - t0) / 1000).toFixed(0)}s] ${s}                    \n`); last = s; }
    if (/已更新|检查完毕|联网失败/.test(s)) break;
  }
  console.log('\n' + '='.repeat(96));
  console.log(`最终状态: ${stat()}   renderAll 调用 ${renderCalls} 次   耗时 ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  console.log('='.repeat(96));
  console.log(`${'id'.padEnd(10)} ${'名称'.padEnd(20)} ${'基座末日'.padEnd(12)} ${'刷新后末日'.padEnd(12)} ${'新增'.padStart(5)}  校验`);
  let bad = 0;
  P.products.forEach(p => {
    const nowLast = p.d[p.d.length - 1], nowN = p.d.length;
    const added = nowN - before[p.id].n;
    // 校验 1：日期严格递增  校验 2：全部为正有限数  校验 3：区间收益合理
    let msg = [];
    for (let i = 1; i < p.d.length; i++) if (!(p.d[i] > p.d[i - 1])) { msg.push('日期非递增'); break; }
    for (let i = 0; i < p.v.length; i++) if (!isFinite(p.v[i]) || p.v[i] <= 0) { msg.push('非法值@' + i); break; }
    const ret = (p.v[p.v.length - 1] / p.v[0] - 1) * 100;
    if (Math.abs(ret) > 100000) msg.push('收益异常');
    if (msg.length) bad++;
    const chg = JSON.stringify([p.d, p.v]) !== before[p.id].sig;
    console.log(p.id.padEnd(10) + ' ' + p.name.slice(0, 16).padEnd(18) + ' '
      + before[p.id].last.padEnd(12) + ' ' + nowLast.padEnd(12) + ' ' + String(added).padStart(5) + '   '
      + (msg.length ? '✗ ' + msg.join(',') : '✓') + `  ${ret.toFixed(2).padStart(10)}%`
      + (chg ? '   ← 有改动' : ''));
  });
  console.log('='.repeat(96));
  const rec = JSON.parse(store.get('nv_live_v1') || 'null');
  console.log(`持久化: ${rec ? Object.keys(rec.items).length + ' 个标的 / ' + Math.round(store.get('nv_live_v1').length / 1024) + ' KB / ' + rec.updated : '未写入'}`);
  console.log(`异常标的数: ${bad}`);
  process.exit(bad ? 1 : 0);
})();
