/* 提取 index.html 内联脚本并做语法检查 + 关键逻辑自测 */
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..');                 // 项目根（tools/ 的上一级）
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

// 抽出最后一个 <script>...</script>（内联主逻辑）
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
console.log('内联 script 块数:', blocks.length, ' 主块长度:', blocks[blocks.length - 1].length);

const main = blocks[blocks.length - 1];
try {
  new Function(main.replace(/\bdocument\b|\bwindow\b|\blocalStorage\b|\brequestAnimationFrame\b/g, 'undefined_x'));
  console.log('[OK] 主脚本语法通过');
} catch (e) {
  console.log('[语法错误]', e.message);
  process.exitCode = 1;
}

// ---- 载入真实数据，纯逻辑自测 ----
const dataSrc = fs.readFileSync(path.join(ROOT, 'assets', 'data.js'), 'utf8');
const window = {};
eval(dataSrc);
const DATA = window.PORTFOLIO_DATA;
if (!DATA) { console.log('[SKIP] 无数据'); process.exit(0); }
console.log(`[数据] ${DATA.products.length} 个标的  更新于 ${DATA.updated}`);

// 复刻页面里的核心函数做口径自测
const parseDate = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const shift = (dt, mo) => new Date(dt.getFullYear(), dt.getMonth() + mo, dt.getDate());
const toKey = dt => dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');

function slice(p, rk, since) {
  const d = p.d, v = p.v, n = d.length;
  if (!n) return null;
  const last = parseDate(d[n - 1]);
  let start = 0;
  if (rk === 'incep') start = 0;
  else if (rk === 'since') {
    if (!since) return { d, v };
    let i = 0; while (i < n && d[i] < since) i++;
    if (i >= n) return null;
    start = Math.max(0, i - 1);
  } else if (rk === 'ytd') {
    let i = 0; while (i < n && d[i] < last.getFullYear() + '-01-01') i++;
    start = Math.max(0, i - 1);
  } else {
    const map = { m1: -1, m3: -3, m6: -6, y1: -12, y3: -36, y5: -60 };
    const t = toKey(shift(last, map[rk]));
    let i = 0; while (i < n && d[i] < t) i++;
    if (i >= n) i = n - 1;
    start = Math.max(0, i - 1);
  }
  return { d: d.slice(start), v: v.slice(start) };
}

const RANGES = ['incep', 'm1', 'm3', 'm6', 'y1', 'y3', 'y5', 'ytd'];
console.log('\n标的'.padEnd(24), RANGES.map(r => r.padStart(10)).join(''));
console.log('-'.repeat(88));
let bad = 0;
for (const p of DATA.products) {
  const cells = RANGES.map(rk => {
    const s = slice(p, rk, '');
    if (!s || s.d.length < 2) return 'n/a'.padStart(10);
    const r = (s.v[s.v.length - 1] / s.v[0] - 1) * 100;
    return ((r >= 0 ? '+' : '') + r.toFixed(2) + '%').padStart(10);
  });
  const nm = (p.name || '').slice(0, 22);
  console.log(nm.padEnd(24), cells.join(''));
  // 健康检查
  for (const [i, x] of p.v.entries()) {
    if (!isFinite(x) || x <= 0) { console.log('   !! 非法值', p.id, p.d[i], x); bad++; break; }
  }
  for (let i = 1; i < p.d.length; i++) {
    if (p.d[i] <= p.d[i - 1]) { console.log('   !! 日期非递增', p.id, p.d[i - 1], p.d[i]); bad++; break; }
  }
}
console.log('\n健康检查：' + (bad === 0 ? '[OK] 无非法值、日期序列全部严格递增' : `[发现 ${bad} 处异常]`));
