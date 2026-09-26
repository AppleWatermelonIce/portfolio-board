/**
 * 验证节流：同一天内第二次打开（reload）不再联网刷新，直接吃 localStorage 增量
 */
const { spawn } = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path');
const HOME = path.resolve(__dirname, '..');                 // 项目根（tools/ 的上一级）
const PAGE = 'file:///' + path.join(HOME, 'index.html').replace(/\\/g, '/');
const CHROME = process.env.CHROME_PATH
  || 'C:/Users/Maple/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe';
const PORT = 9334;
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const profile = path.join(os.tmpdir(), 'cdp-persist');
  fs.mkdirSync(profile, { recursive: true });
  const env = Object.assign({}, process.env);
  ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'].forEach(k => delete env[k]);
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, PAGE], { env, stdio: 'ignore' });
  let ws, id = 0, netCount = 0; const waits = new Map();

  async function waitPort() {
    for (let i = 0; i < 60; i++) {
      try { if ((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok) return; } catch (e) { }
      await sleep(500);
    }
    throw new Error('调试端口未就绪');
  }
  try {
    await waitPort();
    let target;
    for (let i = 0; i < 60; i++) {
      const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      target = l.find(x => x.type === 'page' && x.url.startsWith('file://'));
      if (target) break;
      await sleep(500);
    }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; setTimeout(() => rej(new Error('WS超时')), 10000); });
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.id && waits.has(m.id)) { waits.get(m.id)(m); waits.delete(m.id); return; }
      if (m.method === 'Network.requestWillBeSent') {
        const u = (m.params.request || {}).url || '';
        if (/ifzq\.gtimg\.cn|pingzhongdata|frankfurter/.test(u)) netCount++;
      }
    };
    const send = (method, params) => new Promise(r => { const i = ++id; waits.set(i, r); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
    await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
    const ev = async e => {
      const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) return 'ERR:' + (r.result.exceptionDetails.exception?.description || '').slice(0, 120);
      return r.result?.result?.value;
    };

    console.log('=== 第 1 次打开 ===');
    let s = '';
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      s = await ev(`(document.querySelector('#lstat')||{}).textContent||''`);
      if (/已更新|检查完毕|联网失败/.test(s)) break;
    }
    console.log('  状态:', s);
    console.log('  这轮发起的数据请求数:', netCount);
    const ts1 = await ev(`JSON.parse(localStorage.getItem('nv_live_v1')).ts`);
    const n1 = await ev(`window.PORTFOLIO_DATA.products.map(p=>p.d.length).reduce((a,b)=>a+b,0)`);

    await sleep(1000);
    netCount = 0;
    console.log('\n=== 第 2 次打开（同一天，reload）===');
    await send('Page.reload', { ignoreCache: false });
    await sleep(4000);
    console.log('  状态:', await ev(`(document.querySelector('#lstat')||{}).textContent||''`));
    console.log('  这轮发起的数据请求数:', netCount);
    const ts2 = await ev(`JSON.parse(localStorage.getItem('nv_live_v1')).ts`);
    const n2 = await ev(`window.PORTFOLIO_DATA.products.map(p=>p.d.length).reduce((a,b)=>a+b,0)`);
    console.log('  localStorage 时间戳是否未变:', ts1 === ts2 ? '✓ 未重复写' : '✗ 变了');
    console.log('  序列点数是否一致        :', n1 === n2 ? `✓ ${n1}` : `✗ ${n1} -> ${n2}`);

    console.log('\n=== 点「立即更新」按钮 ===');
    netCount = 0;
    await ev(`document.querySelector('#btnRefresh').click()`);
    for (let i = 0; i < 60; i++) {
      await sleep(1000);
      const t = await ev(`(document.querySelector('#lstat')||{}).textContent||''`);
      if (/已更新|检查完毕|联网失败/.test(t)) { console.log('  状态:', t); break; }
    }
    console.log('  这轮发起的数据请求数:', netCount, netCount > 0 ? '（✓ 手动点击无视节流）' : '（✗ 没联网）');
  } catch (e) {
    console.log('测试失败:', e.message);
  } finally {
    try { ws && ws.close(); } catch (e) { }
    try { chrome.kill(); } catch (e) { }
    setTimeout(() => process.exit(0), 800);
  }
})();
