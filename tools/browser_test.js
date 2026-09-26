/**
 * 用真实 Chrome（headless）打开 file:// 页面，验证浏览器环境的两个关键前提：
 *   ① file:// 页面 fetch 到 https 是否被允许（依赖源站 CORS）
 *   ② localStorage 在 file:// 是否可写
 *   ③ <script src> JSONP（pingzhongdata）能否执行并被读取
 * 驱动方式：CDP over WebSocket（Node 22 自带 WebSocket，无需依赖）
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME = path.resolve(__dirname, '..');                 // 项目根（tools/ 的上一级）
const PAGE = 'file:///' + path.join(HOME, 'index.html').replace(/\\/g, '/');
const CHROME = process.env.CHROME_PATH
  || 'C:/Users/Maple/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe';
const PORT = 9333;

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitPort() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return await r.json();
    } catch (e) { /* 还没起来 */ }
    await sleep(500);
  }
  throw new Error('Chrome 调试端口未就绪');
}

async function pickTarget() {
  for (let i = 0; i < 60; i++) {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    const t = list.find(x => x.type === 'page' && x.url.startsWith('file://'));
    if (t) return t;
    await sleep(500);
  }
  throw new Error('未找到页面 target');
}

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
  // 关键：清掉代理环境变量，否则本地代理会让 Chromium 卡在隧道握手
  const env = Object.assign({}, process.env);
  ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'].forEach(k => delete env[k]);

  // 不加 --allow-file-access-from-files，保持与普通用户双击打开的一致状态
  const chrome = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--no-default-browser-check', '--disable-crash-reporter',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    PAGE,
  ], { env, stdio: 'ignore' });

  let ws, id = 0;
  const waits = new Map();
  const logs = [], errors = [];
  try {
    const ver = await waitPort();
    console.log('浏览器就绪:', ver.Browser);
    const t = await pickTarget();
    ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; setTimeout(() => rej(new Error('WS 超时')), 10000); });
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.id && waits.has(m.id)) { waits.get(m.id)(m); waits.delete(m.id); return; }
      if (m.method === 'Runtime.consoleAPICalled') logs.push((m.params.args || []).map(a => a.value || a.description || a.type).join(' '));
      if (m.method === 'Log.entryAdded') {
        const e = m.params.entry;
        if (e.level === 'error') errors.push(`[${e.source}] ${e.text}` + (e.url ? `  ${e.url}` : ''));
      }
      if (m.method === 'Runtime.exceptionThrown')
        errors.push('EXC ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
    };
    const send = (method, params) => new Promise(res => { const mid = ++id; waits.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params: params || {} })); });

    await send('Runtime.enable'); await send('Log.enable'); await send('Network.enable');
    const ev = async expr => {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description || 'eval error' };
      return r.result?.result?.value;
    };

    console.log('\n—— 前提检查 ——');
    console.log('localStorage 可写 :', await ev(`(function(){try{localStorage.setItem('__t','1');const v=localStorage.getItem('__t');localStorage.removeItem('__t');return v==='1'}catch(e){return 'ERR '+e.message}})()`));
    console.log('跨域 fetch 是否通 :', await ev(`fetch('https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param=${encodeURIComponent('sz000333,day,,,5,hfq')}',{cache:'no-store'}).then(r=>r.status+':'+r.ok).catch(e=>'ERR '+e.message)`));

    console.log('\n—— 等待页面自动刷新 ——');
    let stat = '', t0 = Date.now();
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      const s = await ev(`(document.querySelector('#lstat')||{}).textContent||''`);
      if (s !== stat) { console.log(`  [${((Date.now() - t0) / 1000).toFixed(0)}s] ${s}`); stat = s; }
      if (/已更新|检查完毕|联网失败/.test(s)) break;
    }
    console.log('\n—— 结果 ——');
    console.log('产品数        :', await ev('window.PORTFOLIO_DATA.products.length'));
    console.log('耳目 (Last)  :', await ev(`window.PORTFOLIO_DATA.products.map(p=>p.id+':'+p.d[p.d.length-1]).join('  ')`));
    const ls = await ev(`(function(){const s=localStorage.getItem('nv_live_v1');return s? (Math.round(s.length/1024)+' KB'):'未写入'})()`);
    console.log('localStorage  :', ls);
    console.log('头部状态条    :', await ev(`(document.querySelector('#lstat')||{}).textContent`));
    console.log('新鲜度提示    :', await ev(`(document.querySelector('#fresh')||{}).textContent`));
    console.log('图表已渲染    :', await ev(`document.querySelectorAll('#grid canvas').length`));
    if (errors.length) { console.log('\n控制台错误:'); errors.slice(0, 20).forEach(e => console.log('   ' + e.slice(0, 200))); }
    if (logs.length) { console.log('\n控制台输出:'); logs.slice(0, 20).forEach(l => console.log('   ' + l.slice(0, 200))); }
    console.log('\n完成');
  } catch (e) {
    console.log('测试失败:', e.message);
  } finally {
    try { ws && ws.close(); } catch (e) { }
    try { chrome.kill(); } catch (e) { }
    setTimeout(() => process.exit(0), 800);
  }
})();
