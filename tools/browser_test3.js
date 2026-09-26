/**
 * 估值底色（PE/PB 分位带）实测：真实 Chrome 打开 file:// 页面
 *   ① valuation.js 是否载入
 *   ② 点 PE / PB 后指数卡片是否出现分位徽章
 *   ③ Chart 实例是否收到 valBand 参数、画布是否真的画上了色带（像素采样）
 *   ④ 截图留档
 * 驱动：CDP over WebSocket（Node 22 自带 WebSocket，无需依赖）
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME = path.resolve(__dirname, '..');                 // 项目根（tools/ 的上一级）
const PAGE = 'file:///' + path.join(HOME, 'index.html').replace(/\\/g, '/');
const CHROME = process.env.CHROME_PATH
  || 'C:/Users/Maple/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe';
const PORT = 9336;
const IDS = ['SH000510', 'SH000300', 'SPX', 'NDX100'];

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitPort() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) return await r.json(); }
    catch (e) { /* not yet */ }
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
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    PAGE,
  ], { env, stdio: 'ignore' });

  let ws, id = 0;
  const waits = new Map();
  const errors = [];
  try {
    const ver = await waitPort();
    console.log('浏览器就绪:', ver.Browser);
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

    // 关掉 live.js 的联网（写一条「刚刷新过」的记录），让测试聚焦估值渲染
    await ev(`localStorage.setItem('nv_live_v1', JSON.stringify({ts:Date.now(),updated:'',items:{},tried:{}})); 'ok'`);
    await send('Page.reload', { ignoreCache: false });
    await sleep(3500);

    console.log('\n—— ① 数据载入 ——');
    console.log('VALUATION_DATA :', await ev(`window.VALUATION_DATA ? Object.keys(window.VALUATION_DATA.items).join(',') + '  更新 ' + window.VALUATION_DATA.updated : '未载入'`));
    console.log('5 档定义      :', await ev(`(window.VALUATION_DATA.bands||[]).map(b=>b.name+'<'+Math.round(b.hi*100)+'%:'+b.color).join('  ')`));
    console.log('工具栏估值行  :', await ev(`getComputedStyle(document.querySelector('#valRow')).display`));

    const shot = async name => {
      const r = await send('Page.captureScreenshot', { format: 'png' });
      const p = path.join(HOME, name);
      fs.writeFileSync(p, Buffer.from(r.result.data, 'base64'));
      return p + '  (' + Math.round(fs.statSync(p).size / 1024) + ' KB)';
    };

    for (const kind of ['pe', 'pb']) {
      console.log(`\n—— ② 切到 ${kind.toUpperCase()} ——`);
      await ev(`document.querySelector('#valTabs [data-v="${kind}"]').click(); 'ok'`);
      await sleep(1200);
      // 滚到每个指数卡片，触发懒渲染
      for (const pid of IDS) {
        await ev(`(function(){const c=document.querySelector('.card[data-id="${pid}"]');if(c)c.scrollIntoView({block:'center'});return !!c})()`);
        await sleep(700);
      }
      await sleep(800);
      console.log('图例        :', await ev(`document.querySelector('#valLegend').textContent.trim()`));
      console.log('说明        :', await ev(`document.querySelector('#valNote').textContent`));
      for (const pid of IDS) {
        const badge = await ev(`(document.querySelector('.card[data-id="${pid}"] .vb')||{}).textContent || '（无徽章）'`);
        const plug = await ev(`(function(){const c=document.querySelector('.card[data-id="${pid}"] canvas');if(!c)return 'no-canvas';const ch=Chart.getChart(c);if(!ch)return 'no-chart';const v=ch.options.plugins.valBand;return v&&v.pre? (v.kind+' n='+v.pre.d.length) : '（无色带）'})()`);
        const px = await ev(`(function(){const c=document.querySelector('.card[data-id="${pid}"] canvas');if(!c)return 'no-canvas';const ctx=c.getContext('2d');const w=c.width,h=c.height;if(!w||!h)return '0-size';const d=ctx.getImageData(0,0,w,h).data;let tot=0,col=0;for(let y=Math.floor(h*0.12);y<h*0.88;y+=3){for(let x=Math.floor(w*0.08);x<w*0.92;x+=3){const i=(y*w+x)*4;const a=d[i+3];if(a<4)continue;tot++;const r=d[i],g=d[i+1],b=d[i+2];if(Math.abs(r-g)>14||Math.abs(g-b)>14||Math.abs(r-b)>14)col++;}}return tot? (col+'/'+tot+' = '+(col/tot*100).toFixed(1)+'% 有色'):'空白画布'})()`);
        console.log(`  ${pid.padEnd(9)} 徽章 ${badge.padEnd(26)} 插件 ${String(plug).padEnd(12)} 画布 ${px}`);
      }
      console.log('截图        :', await shot(`val_${kind}.png`));
    }

    // 关掉后应回到无底色
    await ev(`document.querySelector('#valTabs [data-v="off"]').click(); 'ok'`);
    await sleep(1500);
    await ev(`(function(){const c=document.querySelector('.card[data-id="SH000300"]');if(c)c.scrollIntoView({block:'center'});return 1})()`);
    await sleep(800);
    console.log('\n—— ③ 关闭后 ——');
    console.log('徽章数量    :', await ev(`document.querySelectorAll('.card .vb').length`), '（应为 0）');
    console.log('图例        :', await ev(`JSON.stringify(document.querySelector('#valLegend').textContent.trim())`));

    if (errors.length) { console.log('\n控制台错误:'); errors.slice(0, 15).forEach(e => console.log('   ' + e.slice(0, 220))); }
    else console.log('\n控制台错误  : 无 ✓');
    console.log('\n完成');
  } catch (e) {
    console.log('测试失败:', e.message, e.stack);
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { chrome.kill(); } catch (e) {}
  }
})();
