/**
 * 登录链路 E2E：真实 Chrome（headless, CDP）跑「输口令 → 下载分片密文 → 解密 → 解压 → 进入」。
 * 覆盖：错误口令被拒 / 正确口令进入 / 图表渲染 / Cache Storage 复用（二次打开不再走网络）。
 * 用法：先起本地服务（http://127.0.0.1:8799 指向加密后的测试站），再 node tools/_e2e_login.js
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PAGE_URL = process.env.E2E_URL || 'http://127.0.0.1:8799/';
const PASS = process.env.E2E_PASS || 'testpass';
const CHROME = process.env.CHROME_PATH
  || 'C:/Users/Maple/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe';
const PORT = 9345;
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
  const env = Object.assign({}, process.env);
  ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'].forEach(k => delete env[k]);
  const chrome = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--no-default-browser-check', '--disable-crash-reporter',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, 'about:blank',
  ], { env, stdio: 'ignore' });

  let ws = null, id = 0;
  const waits = new Map();
  const errors = [];
  const binMap = new Map();   // requestId → { url, len }（只统计真正走网络的 .enc.bin 分片）

  try {
    for (let i = 0; i < 60; i++) {
      try { if ((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok) break; } catch (e) { }
      await sleep(500);
    }
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    const target = list.find(t => t.type === 'page');
    if (!target) throw new Error('未找到 page target');

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

    const send = (method, params) => new Promise((res, rej) => {
      const i = ++id; waits.set(i, { res, rej });
      ws.send(JSON.stringify({ id: i, method, params }));
    });
    const ev = async expr => {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' | ' + (r.exceptionDetails.exception?.description || ''));
      return r.result.value;
    };
    const waitFor = async (expr, ms) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(500); }
      return false;
    };
    const snapBin = () => [...binMap.values()].filter(x => x.len > 0);

    ws.onmessage = ev2 => {
      const m = JSON.parse(ev2.data);
      if (m.id && waits.has(m.id)) {
        const { res, rej } = waits.get(m.id); waits.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
        return;
      }
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
        errors.push('[log] ' + m.params.entry.text);
      }
      if (m.method === 'Runtime.exceptionThrown') {
        errors.push('[exc] ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
      }
      if (m.method === 'Network.responseReceived') {
        const u = m.params.response.url || '';
        if (u.indexOf('.enc.bin') >= 0) binMap.set(m.params.requestId, { url: u, len: 0 });
      }
      if (m.method === 'Network.loadingFinished') {
        const b = binMap.get(m.params.requestId);
        if (b) b.len = m.params.encodedDataLength;
      }
    };

    await send('Page.enable'); await send('Runtime.enable');
    await send('Log.enable'); await send('Network.enable');

    // ---------- 第 1 轮：冷启动 ----------
    await send('Page.navigate', { url: PAGE_URL });
    await sleep(2500);

    console.log('登录层出现        :', await ev(`(()=>{const b=document.getElementById('login');return !!b && getComputedStyle(b).display!=='none'})()`));
    console.log('账户框默认值      :', await ev(`(document.getElementById('uid')||{}).value`));

    // 错误口令
    await ev(`(()=>{document.getElementById('pass').value='definitely-wrong';
      document.getElementById('loginForm').dispatchEvent(new Event('submit',{cancelable:true}));return 1})()`);
    const wrongMsg = await ev(`(async()=>{for(let i=0;i<160;i++){const t=document.querySelector('#login .lerr').textContent;
      if(/口令错误|超时|失败|不完整/.test(t))return t; await new Promise(r=>setTimeout(r,500));}return document.querySelector('#login .lerr').textContent})()`);
    console.log('错误口令提示      :', JSON.stringify(wrongMsg));

    // 正确口令
    await ev(`(()=>{document.getElementById('pass').value=${JSON.stringify(PASS)};
      document.getElementById('loginForm').dispatchEvent(new Event('submit',{cancelable:true}));return 1})()`);
    const entered = await waitFor(`(()=>{const b=document.getElementById('login');return !!b && getComputedStyle(b).display==='none'})()`, 120000);
    console.log('正确口令后进入    :', entered);
    const okCharts = await waitFor(`document.querySelectorAll('canvas').length>5`, 60000);
    console.log('图表渲染完成      :', okCharts, ' canvas=', await ev(`document.querySelectorAll('canvas').length`),
      ' 标的数=', await ev(`(window.PORTFOLIO_DATA&&window.PORTFOLIO_DATA.products||[]).length`));
    console.log('密文缓存片数      :', await ev(`(async()=>{try{const c=await caches.open('nv-enc-v1');return (await c.keys()).length}catch(e){return 'ERR '+e.message}})()`));
    // 实时角标：等一轮抓取，检查美股指数（曾因 us.INX 带点导致 v_us is not defined）
    await sleep(12000);
    console.log('实时角标 SPX/NDX100:', await ev(`JSON.stringify(((d)=>{const o={};for(const k of ['SPX','NDX100'])o[k]=d[k]?{price:d[k].price,chg:d[k].chg}:null;return o})(window.__rtData||{}))`));
    console.log('实时角标 标的数    :', await ev(`Object.keys(window.__rtData||{}).length`));
    const first = snapBin();
    console.log('第1轮 .bin 请求   :', first.map(x => x.url.replace(PAGE_URL, '/') + ' ' + x.len + 'B').join('  |  ') || '(无)');
    console.log('第1轮密文字节合计 :', first.reduce((a, b) => a + b.len, 0), 'B');

    // ---------- 第 2 轮：应完全命中本地密文缓存 ----------
    binMap.clear();
    await send('Page.navigate', { url: PAGE_URL });
    await sleep(2500);
    await ev(`(()=>{document.getElementById('pass').value=${JSON.stringify(PASS)};
      document.getElementById('loginForm').dispatchEvent(new Event('submit',{cancelable:true}));return 1})()`);
    console.log('第2轮（缓存）进入 :', await waitFor(`(()=>{const b=document.getElementById('login');return !!b && getComputedStyle(b).display==='none'})()`, 60000));
    await sleep(1500);
    console.log('第2轮 .bin 网络请求:', snapBin().length, '片（0 = 完全命中本地缓存）');

    console.log('控制台错误        :', errors.length ? errors.slice(0, 5) : '(无)');
  } catch (e) {
    console.log('E2E FAIL:', e.message);
    process.exitCode = 1;
  } finally {
    try { if (ws) ws.close(); } catch (e) { }
    try { chrome.kill(); } catch (e) { }
    await sleep(500);
    console.log('E2E_DONE');
    process.exit(process.exitCode || 0);
  }
})();
