/**
 * 本轮新增功能实测：① 区间新增「近三年 / 近五年」 ② 估值分位样本窗口 10 年 / 5 年切换
 * 驱动：真实 Chrome + CDP
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME = path.resolve(__dirname, '..');                 // 项目根（tools/ 的上一级）
const PAGE = 'file:///' + path.join(HOME, 'index.html').replace(/\\/g, '/');
const CHROME = process.env.CHROME_PATH
  || 'C:/Users/Maple/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe';
const PORT = 9337;
const IDS = ['SH000510', 'SH000300', 'SPX', 'NDX100'];

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function waitPort() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) return await r.json(); } catch (e) {}
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
  const env = Object.assign({}, process.env);
  ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'].forEach(k => delete env[k]);

  const chrome = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--no-default-browser-check', '--disable-crash-reporter',
    '--window-size=1600,1200', '--force-device-scale-factor=1',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, PAGE,
  ], { env, stdio: 'ignore' });

  let ws, id = 0;
  const waits = new Map();
  const errors = [];
  try {
    const ver = await waitPort();
    const t = await pickTarget();
    ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; setTimeout(() => rej(new Error('WS 超时')), 10000); });
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.id && waits.has(m.id)) { waits.get(m.id)(m); waits.delete(m.id); return; }
      if (m.method === 'Runtime.exceptionThrown')
        errors.push('EXC ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error')
        errors.push(`[${m.params.entry.source}] ${m.params.entry.text}`);
    };
    const send = (method, params) => new Promise(res => { const mid = ++id; waits.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params: params || {} })); });
    await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
    const ev = async expr => {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description || 'eval error' };
      return r.result?.result?.value;
    };

    await ev(`localStorage.setItem('nv_live_v1', JSON.stringify({ts:Date.now(),updated:'',items:{},tried:{}})); 'ok'`);
    await send('Page.reload', { ignoreCache: false });
    await sleep(3500);

    const focus = async (pid) => {
      await ev(`(function(){const c=document.querySelector('.card[data-id="${pid}"]');if(c)c.scrollIntoView({block:'center'});return !!c})()`);
      await sleep(600);
    };
    const shot = async name => {
      const r = await send('Page.captureScreenshot', { format: 'png' });
      const p = path.join(HOME, name);
      fs.writeFileSync(p, Buffer.from(r.result.data, 'base64'));
      return p + ' (' + Math.round(fs.statSync(p).size / 1024) + ' KB)';
    };

    console.log('浏览器:', ver.Browser);

    /* ---------- ① 区间标签 ---------- */
    console.log('\n—— ① 区间标签 ——');
    console.log('标签      :', await ev(`[...document.querySelectorAll('#ranges .tab')].map(b=>b.textContent).join(' / ')`));
    for (const rk of ['y1', 'y3', 'y5', 'incep']) {
      await ev(`document.querySelector('#ranges [data-r="${rk}"]').click(); 'ok'`);
      await sleep(900);
      await focus('SH000300');
      const rng = await ev(`(function(){const c=document.querySelector('.card[data-id="SH000300"] canvas');const ch=Chart.getChart(c);if(!ch)return 'no-chart';const L=ch.data.labels;return L[0]+' ~ '+L[L.length-1]+'  ('+L.length+' 点)'})()`);
      console.log(`  ${rk.padEnd(6)} → ${rng}`);
    }
    console.log('截图      :', await shot('range_y3.png'));

    /* ---------- ② 估值样本窗口 ---------- */
    await ev(`document.querySelector('#ranges [data-r="incep"]').click(); 'ok'`);
    await sleep(800);
    await ev(`document.querySelector('#valTabs [data-v="pe"]').click(); 'ok'`);
    await sleep(1000);

    for (const win of [10, 5]) {
      console.log(`\n—— ② PE · ${win} 年样本 ——`);
      await ev(`document.querySelector('#valWinTabs [data-y="${win}"]').click(); 'ok'`);
      await sleep(1000);
      console.log('窗口按钮  :', await ev(`[...document.querySelectorAll('#valWinTabs .tab')].map(b=>b.textContent+(b.classList.contains('on')?'★':'')).join(' ')`));
      console.log('说明      :', await ev(`document.querySelector('#valNote').textContent`));
      for (const pid of IDS) {
        await focus(pid);
        const badge = await ev(`(document.querySelector('.card[data-id="${pid}"] .vb')||{}).textContent || '（无）'`);
        const pre = await ev(`(function(){const c=document.querySelector('.card[data-id="${pid}"] canvas');const ch=Chart.getChart(c);if(!ch)return 'no-chart';const v=ch.options.plugins.valBand;return v&&v.pre? (v.pre.win+'y 窗口 n='+v.pre.nWin+' / 全序列 '+v.pre.nAll) : '（无色带）'})()`);
        const px = await ev(`(function(){const c=document.querySelector('.card[data-id="${pid}"] canvas');if(!c)return 'no';const ctx=c.getContext('2d');const w=c.width,h=c.height;const d=ctx.getImageData(0,0,w,h).data;let tot=0,col=0;for(let y=Math.floor(h*0.12);y<h*0.88;y+=3){for(let x=Math.floor(w*0.08);x<w*0.92;x+=3){const i=(y*w+x)*4;if(d[i+3]<4)continue;tot++;const r=d[i],g=d[i+1],b=d[i+2];if(Math.abs(r-g)>14||Math.abs(g-b)>14||Math.abs(r-b)>14)col++;}}return tot?(col/tot*100).toFixed(1)+'% 有色':'空白'})()`);
        console.log(`  ${pid.padEnd(9)} ${String(badge).padEnd(24)} ${String(pre).padEnd(28)} ${px}`);
      }
      console.log('截图      :', await shot(`val_pe_${win}y.png`));
    }

    // PB + 5 年
    console.log('\n—— ③ PB · 5 年样本 ——');
    await ev(`document.querySelector('#valTabs [data-v="pb"]').click(); 'ok'`);
    await sleep(1000);
    await ev(`document.querySelector('#valWinTabs [data-y="5"]').click(); 'ok'`);
    await sleep(1000);
    console.log('说明      :', await ev(`document.querySelector('#valNote').textContent`));
    for (const pid of IDS) {
      await focus(pid);
      console.log(`  ${pid.padEnd(9)}`, await ev(`(document.querySelector('.card[data-id="${pid}"] .vb')||{}).textContent || '（无）'`));
    }

    console.log('\n错误      :', errors.length ? errors.slice(0, 10) : '无 ✓');
    console.log('\n完成');
  } catch (e) {
    console.log('测试失败:', e.message);
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { chrome.kill(); } catch (e) {}
  }
})();
