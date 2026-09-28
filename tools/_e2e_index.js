/**
 * 指数组渲染验证（含新增国金A500指增 022485）：headless Chrome + CDP，file:// 直开明文站
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const FILE = 'file:///' + path.join(__dirname, '..', 'index.html').replace(/\\/g, '/');
const CHROME = process.env.CHROME_PATH
  || 'C:/Users/Maple/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe';
const PORT = 9361;
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-idx-'));
  const env = Object.assign({}, process.env);
  ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'].forEach(k => delete env[k]);
  const chrome = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--no-default-browser-check', '--disable-crash-reporter',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, 'about:blank',
  ], { env, stdio: 'ignore' });

  let ws = null, id = 0;
  const waits = new Map(); const errors = [];
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
      const i = ++id; waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params }));
    });
    const ev = async expr => {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' | ' + (r.exceptionDetails.exception?.description || ''));
      return r.result.value;
    };
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.id && waits.has(m.id)) {
        const { res, rej } = waits.get(m.id); waits.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); return;
      }
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push('[log] ' + m.params.entry.text);
      if (m.method === 'Runtime.exceptionThrown') errors.push('[exc] ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
    };
    await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');

    await send('Page.navigate', { url: FILE });
    await sleep(3000);
    console.log('登录层隐藏      :', await ev(`(()=>{const b=document.getElementById('login');return !b || getComputedStyle(b).display==='none'})()`));
    console.log('标的总数        :', await ev(`DATA.products.length`));
    console.log('022485 在清单   :', await ev(`!!DATA.products.find(p=>p.id==='022485')`));
    console.log('022485 序列长度 :', await ev(`(()=>{const p=DATA.products.find(x=>x.id==='022485');return p&&p.d?p.d.length:0})()`));
    console.log('022485 末值日期 :', await ev(`(()=>{const p=DATA.products.find(x=>x.id==='022485');return p&&p.d?p.d.slice(-1)[0]:null})()`));

    await ev(`(()=>{const b=[...document.querySelectorAll('#groups .chip')].find(x=>x.dataset.g==='指数');if(!b)return 0;b.click();return 1})()`);
    await sleep(1500);
    await ev(`(async()=>{for(let y=0;y<document.body.scrollHeight;y+=600){window.scrollTo(0,y);await new Promise(r=>setTimeout(r,180));}window.scrollTo(0,0);return 1})()`);
    await sleep(1800);

    const seq = await ev(`[...document.querySelectorAll('#grid > *')].map(e=>{
      if(e.classList.contains('slothead')) return '## ' + e.textContent.trim();
      const n=(e.querySelector('.cname')||{}).textContent||'';
      const m=(e.querySelector('.cmeta')||{}).textContent||'';
      const s=(e.querySelectorAll('.stat')||[]).length;
      return '   ' + n.trim() + '  |  ' + m.trim() + '  | stats=' + s;
    })`);
    console.log('\n--- 指数组视图 DOM 序列 ---');
    seq.forEach(s => console.log(s));
    console.log('\n卡片数          :', seq.length);
    console.log('已渲染图表数    :', await ev(`[...document.querySelectorAll('#grid canvas')].filter(c=>c.width>400).length`));

    await ev(`(()=>{window.scrollTo(0,0);return 1})()`);
    await sleep(800);
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 2000, deviceScaleFactor: 1, mobile: false });
    await ev(`(()=>{const g=document.getElementById('grid');if(g)window.scrollTo(0,g.offsetTop-20);return 1})()`);
    await sleep(900);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    const out = path.join(__dirname, '_screenshots', 'index_group.png');
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, Buffer.from(shot.data, 'base64'));
    console.log('截图            :', out);
    console.log('控制台错误      :', errors.length ? errors : '无');
  } catch (e) {
    console.error('E2E 失败:', e.message);
    process.exitCode = 1;
  } finally {
    if (ws) try { ws.close(); } catch (e) { }
    chrome.kill();
  }
})();
