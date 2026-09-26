/* 静态一致性检查：id 引用、函数定义、CSS 类 */
const fs = require('fs');
const ROOT = require('path').join(__dirname, '..');      // 项目根（tools/ 的上一级）
const html = fs.readFileSync(ROOT + '/index.html', 'utf8');

// 1. HTML 中定义的 id
const defined = new Set([...html.matchAll(/\sid="([A-Za-z0-9_-]+)"/g)].map(m => m[1]));
console.log('HTML 中定义的 id:', [...defined].join(', '));

// 2. JS 中 $('#xx') / getElementById 引用的 id
const scriptBlocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const js = scriptBlocks[scriptBlocks.length - 1];
const used = new Set();
for (const m of js.matchAll(/\$\('#([A-Za-z0-9_-]+)'\)/g)) used.add(m[1]);
for (const m of js.matchAll(/getElementById\('([A-Za-z0-9_-]+)'\)/g)) used.add(m[1]);

// 运行时动态注入到 innerHTML 里的 id（模板字符串中 id= 的部分）
const dynamic = new Set();
for (const m of js.matchAll(/id="([A-Za-z0-9_-]+)"/g)) dynamic.add(m[1]);
for (const m of js.matchAll(/id=\\?"?([A-Za-z0-9_-]+)/g)) dynamic.add(m[1]);

console.log('\nJS 静态引用的 id:', [...used].sort().join(', '));
console.log('动态生成的 id:', [...dynamic].filter(d => /\$\{|/.test(d) || !d.includes('$')).join(', '));

// 静态引用但未定义（排除动态生成的）
const missing = [...used].filter(u => !defined.has(u) && !dynamic.has(u));
console.log('\n[检查1] 静态引用但页面/template 中不存在的 id:', missing.length ? missing : '无 ✓');

// 3. 函数调用 vs 定义
const defs = new Set([...js.matchAll(/function\s+([A-Za-z_$][\w$]*)\s*\(/g)].map(m => m[1]));
const arrows = new Set([...js.matchAll(/(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*\(/g)].map(m => m[1]));
console.log('\n定义的函数:', [...defs, ...arrows].sort().join(', '));

// 检查所有 裸调用 foo( 是否已定义或是内置
const builtins = new Set(['if','for','while','switch','catch','typeof','return','function','Math','JSON','Date','Set','Map','Array','Object','String','Number','parseInt','parseFloat','isNaN','encodeURIComponent','decodeURIComponent','requestAnimationFrame','setTimeout','setInterval','clearTimeout','Chart','console','Array','Promise','new','else','do','try','of','in','void','delete','instanceof','super','this','window','document','localStorage','navigator','URL','Blob','IntersectionObserver','addEventListener','parseDate','toKey','daysBetween','shift',
  // 以下为误报：async/await 是关键字，beforeDatasetsDraw 是 Chart.js 插件钩子，
  // rgba()/var() 出现在 CSS 模板字符串里
  'async','await','beforeDatasetsDraw','rgba','var']);
const called = new Set([...js.matchAll(/(?<![\w$.'"])([A-Za-z_$][\w$]*)\s*\(/g)].map(m => m[1]));
const undef = [...called].filter(c => !defs.has(c) && !arrows.has(c) && !builtins.has(c)
  && !(c[0] === c[0].toUpperCase())   // 构造函数/类
  && !new RegExp('(const|let|var|function|=>\\s*|\\()\\s*' + c.replace(/\$/g,'\\$') + '\\b').test(js)
);
console.log('\n[检查2] 可能未定义的调用:', undef.length ? undef.sort().join(', ') : '无 ✓');

// 4. CSS 类：JS 中使用的 class 是否在 <style> 中定义
const cssClasses = new Set([...html.matchAll(/\.([a-zA-Z][\w-]*)\s*[,{:]/g)].map(m => m[1]));
const usedClasses = new Set([...html.matchAll(/class="([^"]+)"/g)].flatMap(m => m[1].split(/\s+/)));
for (const m of js.matchAll(/class="([^"]*)"/g)) m[1].split(/\s+/).forEach(c => c && usedClasses.add(c));
for (const m of js.matchAll(/classList\.(?:add|toggle|remove)\('([^']+)'/g)) usedClasses.add(m[1]);
const noCss = [...usedClasses].filter(c => c && !cssClasses.has(c));
console.log('\n[检查3] 用到但 CSS 未定义的 class:', noCss.length ? noCss.join(', ') : '无 ✓');

// 5. 关键功能点自检
const checks = [
  ['9 个区间标签齐全', ['incep','since','m1','m3','m6','y1','y3','y5','ytd'].every(k => js.includes(k))],
  ['近三年/近五年有切片映射', js.includes('y3:-36') && js.includes('y5:-60')],
  ['估值 PE/PB 开关', js.includes("['pe','PE']") && js.includes("['pb','PB']") && html.includes('./assets/valuation.js')],
  ['估值 10/5 年样本窗口', js.includes('valWin') && js.includes('VAL_WINS') && js.includes('valWinTabs')],
  ['收益归一化到起点 0%', /v\[0\]\s*-\s*1/.test(js)],
  ['每个产品单独一个图', js.includes('cardHTML') && js.includes('<canvas>')],
  ['支持增删标的', js.includes('btnAdd') && js.includes('btnDelMode') && js.includes('rebuildProductsJSON')],
  ['本地 Chart.js 引用', html.includes('vendor/chart.umd.min.js')],
  ['本地 data.js 引用', html.includes('./assets/data.js')],
  ['目录结构完整', ['index.html', 'assets/data.js', 'assets/valuation.js', 'assets/live.js',
                    'assets/vendor/chart.umd.min.js', 'data/products.json']
    .every(f => fs.existsSync(require('path').join(ROOT, f)))],
  ['红涨绿跌', html.includes('--up:#d0382f') && html.includes('--down:#12a05c')],
  ['浅色主题', html.includes('--bg:#f6f6f7')],
  ['前复权负值防御', js.includes('sanitize')],
  ['数据陈旧告警', js.includes('数据已') || js.includes('ageH > 48')],
];
console.log('\n[检查4] 功能点：');
let fail = 0;
for (const [name, ok] of checks) { console.log(`  ${ok ? '✓' : '✗'} ${name}`); if (!ok) fail++; }
process.exitCode = (missing.length || undef.length || fail) ? 1 : 0;
