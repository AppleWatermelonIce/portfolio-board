/* ============================================================================
 * live.js —— 「打开即更新」前端增量刷新
 *
 * 设计要点
 *   1. data.js 是基座（全历史），由 update_data.py 生成；本文件只补「尾部增量」。
 *   2. 增量结果存 localStorage（nv_live_v1），尾部保留 45 个点用于校验与对齐。
 *   3. 节流：距上次成功刷新不足 SCAN_GAP_MS 则不联网；页面常开时每 30 分钟自查一次。
 *   4. 通道（均为实测可用<em>无 Referer / 带 CORS 或可 JSONP</em>）：
 *        股票·ETF·指数·黄金 → 腾讯 newfqkline（fetch，ACAO=*，后复权 hfq）
 *        标普500 / 纳指100   → 腾讯 newfqkline（us.INX / us.NDX，不复权）
 *        开放式基金          → 天天基金 pingzhongdata（<script src>，体积大，带门控）
 *        美元/人民币          → Frankfurter(ECB) 区间汇率（fetch，ACAO=*，无需 key）
 *   5. 合并时先看重叠区比值：整体缩放比例 != 1 说明基座换过源/口径，
 *      按中位数比值对齐后再拼接，保证收益率连续。无重叠则拒绝写入（宁缺勿错）。
 * ========================================================================== */
(function () {
'use strict';

// 数据由 index.html 解密启动器在口令校验后注入 window.PORTFOLIO_DATA，再调用 __liveBoot
let D = null;

const KEY        = 'nv_live_v1';
const SCAN_GAP   = 6 * 3600 * 1000;   // 6 小时内不重复联网（一天打开多次只刷一次）
const TAIL_N     = 45;                // 每个标的缓存的尾部点数
const PZ_CONC    = 1;                 // pingzhongdata 串行（文件大，且会写同名全局）
const TX_CONC    = 4;

const TX_HOSTS = [
  'https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get',
  'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get',
];

/* ------------------------------------------------------------------ 工具 */
const pad = n => String(n).padStart(2, '0');
const $   = s => document.querySelector(s);
function bjParts(ts) {
  const t = new Date((ts == null ? Date.now() : ts) + 8 * 3600e3);
  return {
    key:  t.getUTCFullYear() + '-' + pad(t.getUTCMonth() + 1) + '-' + pad(t.getUTCDate()),
    hour: t.getUTCHours() + t.getUTCMinutes() / 60,
    dow:  t.getUTCDay(),
  };
}
const bjStamp = ts => {
  const t = new Date((ts == null ? Date.now() : ts) + 8 * 3600e3);
  return t.getUTCFullYear() + '-' + pad(t.getUTCMonth() + 1) + '-' + pad(t.getUTCDate())
       + ' ' + pad(t.getUTCHours()) + ':' + pad(t.getUTCMinutes());
};

/** 简易并发池，失败不中断 */
async function pool(items, n, fn) {
  const out = new Array(items.length);
  let i = 0;
  const worker = async () => {
    while (i < items.length) {
      const k = i++;
      try { out[k] = { ok: true, v: await fn(items[k]) }; }
      catch (e) { out[k] = { ok: false, e: (e && e.message) || String(e) }; }
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(n, items.length)) }, worker));
  return out;
}

/* ------------------------------------------------------------ 通道解析 */
function channelOf(p) {
  if (p.src === 'goldcny') return { ch: 'skip' };   // 人民币金价由每日批处理（雅虎×ECB）刷新，浏览器端无雅虎 CORS
  if (p.src === 'fund') return { ch: 'pz', id: String(p.secid || p.code).padStart(6, '0') };
  if (p.id === 'SPX')    return { ch: 'tx', id: 'us.INX', fq: '' };
  if (p.id === 'NDX100') return { ch: 'tx', id: 'us.NDX', fq: '' };
  if (p.src === 'fx' || p.secid === 'USDCNY=X' || /^USDCN/i.test(p.code || ''))
    return { ch: 'fr', id: /^[A-Z]{3}-[A-Z]{3}$/.test(p.secid || '') ? p.secid : 'USD-CNY' };
  const m = String(p.secid || '').match(/^(\d+)\.(.+)$/);
  if (m) {
    const pre = { '0': 'sz', '1': 'sh', '116': 'hk' }[m[1]];
    if (pre) return { ch: 'tx', id: pre + (m[1] === '116' ? m[2].padStart(5, '0') : m[2]), fq: 'hfq' };
  }
  return { ch: null };
}

/* -------------------------------------------------- ① 腾讯日K（fetch） */
async function txGet(code, count, fq) {
  const param = code + ',day,,,' + count + ',' + (fq || '');
  let last = '无响应';
  for (const host of TX_HOSTS) {
    try {
      const r = await fetch(host + '?param=' + encodeURIComponent(param), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const kk = ((await r.json()).data || {})[code] || {};
      const arr = kk.hfqday || kk.qfqday || kk.day || [];
      if (arr.length) return arr;
      last = '返回空';
    } catch (e) { last = (e && e.message) || String(e); }
  }
  throw new Error(last);
}
const txRows = arr => arr
  .map(x => [x[0], parseFloat(x[2])])
  .filter(x => x[0] && isFinite(x[1]));

/** 需要的根数：按本地末点距今天数折算交易日，留足重叠区 */
function needBars(p, nowKey) {
  if (!p.d || !p.d.length) return 640;
  const gap = (Date.parse(nowKey) - Date.parse(p.d[p.d.length - 1])) / 864e5;
  return Math.min(640, Math.max(60, Math.round(gap * 0.72) + 15));
}

/* ------------------------------------------- ② 天天基金 pingzhongdata */
function loadScript(src, timeout) {
  return new Promise((res, rej) => {
    const s = document.createElement('script');
    let done = false;
    const fin = err => { if (done) return; done = true; clearTimeout(t); s.remove(); err ? rej(err) : res(); };
    const t = setTimeout(() => fin(new Error('超时')), timeout || 25000);
    s.src = src; s.async = true;
    s.onload  = () => fin(null);
    s.onerror = () => fin(new Error('加载失败'));
    document.head.appendChild(s);
  });
}

function parseUnitMoney(s) {
  let div = 0, ratio = 1;
  if (!s) return [div, ratio];
  s = String(s);
  let m = s.match(/派现金\s*([0-9]*\.?[0-9]+)\s*元/);   if (m) div = parseFloat(m[1]) || 0;
  m = s.match(/折算成?\s*([0-9]*\.?[0-9]+)\s*份/);      if (m) ratio = parseFloat(m[1]) || 1;
  return [div, ratio];
}

/** 红利再投资复权净值 —— 与 update_data.py fetch_fund_nav 同一口径 */
function navSeries(trend) {
  const dates = [], navs = [], divs = [], splits = [];
  for (const it of trend) {
    if (it.x == null || it.y == null) continue;
    const [dv, rt] = parseUnitMoney(it.unitMoney || '');
    const t = new Date(it.x + 8 * 3600e3);      // 时间戳按北京时间折算日期
    dates.push(t.getUTCFullYear() + '-' + pad(t.getUTCMonth() + 1) + '-' + pad(t.getUTCDate()));
    navs.push(parseFloat(it.y)); divs.push(dv); splits.push(rt);
  }
  const n = navs.length;
  if (!n) throw new Error('净值序列为空');

  const eff = [], divEff = [];
  let cum = 1;
  for (let i = 0; i < n; i++) { cum *= splits[i]; eff.push(navs[i] * cum); divEff.push(divs[i] * cum); }

  const R = [1]; let r = 1;
  for (let i = 1; i < n; i++) {
    const prev = eff[i - 1];
    if (prev && prev > 0) r *= 1 + (eff[i] + divEff[i] - prev) / prev;
    R.push(r);
  }
  const base = navs[0];
  const map = new Map();
  for (let i = 0; i < n; i++) map.set(dates[i], +(R[i] * base).toFixed(6));
  const ds = [...map.keys()].sort();
  return ds.map(d => [d, map.get(d)]);
}

async function pzGet(code) {
  window.Data_netWorthTrend = null;              // 防止上一只基金的残留被误读
  await loadScript('https://fund.eastmoney.com/pingzhongdata/' + code + '.js', 30000);
  const tr = window.Data_netWorthTrend;
  if (!tr || !tr.length) throw new Error('无净值数据');
  return navSeries(tr);
}

/* ------------------------------------- ③ Frankfurter(ECB) 日频汇率 */
/** 取 [起点, 今天] 的汇率序列；起点按本地末点回退若干天，保证有重叠可校验 */
async function frRange(pair, sinceDate) {
  const [b, q] = pair.split('-');
  const start = sinceDate
    ? new Date(Date.parse(sinceDate) - 12 * 864e5).toISOString().slice(0, 10)
    : new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  const end = new Date().toISOString().slice(0, 10);
  // ⚠ 必须直连 api.frankfurter.dev：旧域名 .app 会 301 过来，
  //   而 301 响应不带 ACAO，浏览器会被 CORS 拦下（Python 能跟随跳转，浏览器不行）
  const r = await fetch(`https://api.frankfurter.dev/v1/${start}..${end}?from=${b}&to=${q}`, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const rates = (await r.json()).rates || {};
  return Object.keys(rates).sort().map(d => [d, rates[d][q]]).filter(x => x[1] > 0);
}

/* ------------------------------------------- ④ 实时价角标（仅展示，不并入收益率曲线）
   - A股/ETF/指数/港股/美股指数：腾讯 qt.gtimg.cn（<script src> JSONP，不受 CORS 限制）
   - 开放式基金：天天基金 fundgz（<script src>，日内估算净值 gsz + gszzl）；被 ASN 拦截时降级「—」
   - 黄金(goldcny)：GoldAPI 现货 XAU/USD（fetch，CORS=*，秒级，免 key）× 实时美兑人 → 折算 元/克（仅展示价，无涨跌）
   - 汇率(fx)：open.er-api.com 美兑人（fetch，CORS=*，小时级，免 key）（仅展示价，无涨跌）
   颜色遵循 A股习惯：红涨绿跌。 */
const RT = { data:{}, running:false };
window.__rtData = RT.data;                 // 供 index.html 渲染卡片时回放缓存

function rtCodeOf(p){
  if (p.src === 'goldcny') return {mode:'gold'};
  if (p.src === 'fx')     return {mode:'fx'};
  if (p.src === 'fund')   return {mode:'fund', code: String(p.secid || p.code).padStart(6,'0')};
  const c = channelOf(p);
  // 关键：实时角标走的是 <script src> JSONP，腾讯会把代码原样用作变量名——
  // 带点的代码（如 us.INX）会返回 `v_us.INX="..."`，那是「对变量 v_us 取属性」→
  // 抛 ReferenceError: v_us is not defined，且会中断整条脚本、连带同批其它代码全部拿不到值。
  // 腾讯对无点写法（usINX / usNDX）返回完全相同的数据，故此处统一去掉点。
  // 注意：日线路径（newfqkline，JSON 接口）不走这里，仍按原样使用 us.INX / us.NDX。
  if (c.ch === 'tx') return {mode:'tx', code:String(c.id).replace(/\./g, '')};
  return {mode:'none'};
}
function parseTx(str){
  if (!str || typeof str !== 'string') return null;
  const f = str.split('~');
  if (f.length < 33) return null;
  const price = parseFloat(f[3]);
  const prev  = parseFloat(f[4]);
  if (!(price > 0)) return null;
  let chg = parseFloat(f[32]);                       // 腾讯自带涨跌幅%，直接用
  if (!(prev > 0) || !isFinite(chg)) chg = (price - prev) / prev * 100;
  if (!isFinite(chg)) return null;
  return {price, chg, ts: (f[30] || '').replace(/\//g,'-')};
}
function loadTxScript(codes){
  return new Promise(res => {
    const url = 'https://qt.gtimg.cn/q=' + encodeURIComponent(codes.join(',')) + '&_=' + Date.now();
    const s = document.createElement('script');
    let done = false;
    const fin = () => { if (done) return; done = true; clearTimeout(t); try{s.remove();}catch(e){} res(true); };
    const t = setTimeout(fin, 9000);
    s.charset = 'GBK';
    s.src = url; s.onload = fin; s.onerror = fin;
    document.head.appendChild(s);
  });
}
const _fundWaiters = new Map();
window.jsonpgz = function(d){
  if (!d || !d.fundcode) return;
  const w = _fundWaiters.get(d.fundcode);
  if (w && !w.settled){ w.settled = true; clearTimeout(w.t); try{w.s.remove();}catch(e){} w.res(d); }
};
function loadFundScript(code){
  return new Promise(res => {
    const s = document.createElement('script');
    const w = {res, s, settled:false, t:null};
    w.t = setTimeout(() => { if(!w.settled){ w.settled=true; _fundWaiters.delete(code); try{s.remove();}catch(e){} res(null); } }, 9000);
    _fundWaiters.set(code, w);
    s.onload  = () => { if(!w.settled){ w.settled=true; clearTimeout(w.t); _fundWaiters.delete(code); try{s.remove();}catch(e){} res(null); } };
    s.onerror = () => { if(!w.settled){ w.settled=true; clearTimeout(w.t); _fundWaiters.delete(code); try{s.remove();}catch(e){} res(null); } };
    s.src = 'https://fundgz.1234567.com.cn/js/' + code + '.js?rt=' + Date.now();
    document.head.appendChild(s);
  });
}
function fundInfo(d){
  const gsz = parseFloat(d.gsz), dwjz = parseFloat(d.dwjz), chg = parseFloat(d.gszzl);
  const price = (isFinite(gsz) && gsz > 0) ? gsz : (isFinite(dwjz) ? dwjz : null);
  if (price == null || !isFinite(chg)) return null;
  const isEst = isFinite(gsz) && gsz > 0;
  return {price, chg, dp:4, tag: isEst ? '估' : '净', ts: d.gztime || ''};
}
function inTradingNow(){
  const t = new Date(Date.now() + 8*3600e3);          // 北京时间
  const dow = t.getUTCDay();
  if (dow === 0 || dow === 6) return false;
  const hm = t.getUTCHours()*60 + t.getUTCMinutes();
  return (hm >= 570 && hm <= 690) || (hm >= 780 && hm <= 900);   // 09:30–11:30 / 13:00–15:00
}

/* ------------------------------------- ④b 商品/汇率 实时（fetch, CORS=*，免 key） */
const TROY_OZ_G = 31.1034768;          // 1 金衡盎司 = 克
/** USD→CNY 汇率（小时级） */
async function fetchFxRate(){
  const r = await fetch('https://open.er-api.com/v6/latest/USD', {cache:'no-store'});
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const j = await r.json();
  if (!j || j.result !== 'success' || !j.rates || !(j.rates.CNY > 0)) throw new Error('无 CNY 汇率');
  const ts = bjStamp(j.time_last_update_unix ? j.time_last_update_unix * 1000 : Date.now());
  return { cny: +j.rates.CNY, ts };
}
/** XAU/USD 现货（秒级） */
async function fetchGoldUsd(){
  const r = await fetch('https://api.gold-api.com/price/XAU', {cache:'no-store'});
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const j = await r.json();
  if (!j || !(j.price > 0)) throw new Error('无金价');
  const ts = bjStamp(j.updatedAt ? Date.parse(j.updatedAt) : Date.now());
  return { price: +j.price, ts };
}
/** 商品/汇率实时角标：黄金=现货XAU×美兑人折算元/克；汇率=美兑人。源不提供前收，故无涨跌。 */
async function fetchSpotRealtime(){
  const fxList   = D.products.filter(p => p.src === 'fx');
  const goldList = D.products.filter(p => p.src === 'goldcny');
  if (!fxList.length && !goldList.length) return;
  let fx = null;
  try { fx = await fetchFxRate(); } catch (e) { fx = null; }
  for (const p of fxList){
    const info = fx ? {price: fx.cny, chg: null, dp: 4, tag: '汇', ts: fx.ts} : null;
    RT.data[p.id] = info;
    if (window.__rtPaint) window.__rtPaint(p.id, info);
  }
  if (goldList.length){
    let rate = fx ? fx.cny : null;
    if (rate == null){ try { rate = (await fetchFxRate()).cny; } catch (e) { rate = null; } }
    try {
      const g = await fetchGoldUsd();
      if (rate != null){
        const cnyPerGram = g.price * rate / TROY_OZ_G;
        const info = {price: cnyPerGram, chg: null, dp: 2, tag: '现', ts: g.ts};
        for (const p of goldList){ RT.data[p.id] = info; if (window.__rtPaint) window.__rtPaint(p.id, info); }
      } else {
        for (const p of goldList){ RT.data[p.id] = null; if (window.__rtPaint) window.__rtPaint(p.id, null); }
      }
    } catch (e) {
      for (const p of goldList){ RT.data[p.id] = null; if (window.__rtPaint) window.__rtPaint(p.id, null); }
    }
  }
}
async function fetchRealtime(){
  if (RT.running) return;
  RT.running = true;
  try {
    const txCodes = [], txMap = new Map();
    const fundCodes = [], fundMap = new Map();
    for (const p of D.products){
      const r = rtCodeOf(p);
      if (r.mode === 'tx'){ txCodes.push(r.code); txMap.set(r.code, p); }
      else if (r.mode === 'fund'){ fundCodes.push(r.code); fundMap.set(r.code, p); }
    }
    for (let i = 0; i < txCodes.length; i += 40){
      const chunk = txCodes.slice(i, i+40);
      await loadTxScript(chunk);
      for (const code of chunk){
        const p = txMap.get(code); if (!p) continue;
        const info = window['v_'+code] ? parseTx(window['v_'+code]) : null;
        if (info){ info.dp = 2; info.tag = '盘'; }
        RT.data[p.id] = info || null;
        if (window.__rtPaint) window.__rtPaint(p.id, info);
      }
    }
    await pool(fundCodes, 3, async (code) => {
      const p = fundMap.get(code); if (!p) return;
      const d = await loadFundScript(code);
      const info = d ? fundInfo(d) : null;
      RT.data[p.id] = info || null;
      if (window.__rtPaint) window.__rtPaint(p.id, info);
    });
    await fetchSpotRealtime();              // 商品/汇率：24h×工作日，随时刷新
  } catch(e){ /* 实时角标失败不影响主曲线 */ }
  finally { RT.running = false; }
}

/* ------------------------------------------------------------- 合并写回 */
/** 返回 {added, changed}；-1 表示重叠不足不敢合并 */
function applyPoints(p, pts) {
  const m = new Map();
  for (let i = 0; i < p.d.length; i++) m.set(p.d[i], p.v[i]);
  if (!m.size) return { added: 0, changed: 0 };

  const ratios = [];
  for (const [d, v] of pts) {
    if (m.has(d) && m.get(d) > 0 && v > 0) ratios.push(v / m.get(d));
  }
  if (!ratios.length) return null;                       // 无重叠 → 拒绝
  ratios.sort((a, b) => a - b);
  const med = ratios[ratios.length >> 1];
  const k = Math.abs(med - 1) > 0.0005 ? med : 1;        // 复权基准变化 → 整体对齐

  let added = 0, changed = 0;
  for (const [d, v] of pts) {
    const nv = v / k;
    if (!m.has(d)) added++;
    else if (Math.abs(m.get(d) - nv) > 1e-9) changed++;
    m.set(d, nv);
  }
  const ds = [...m.keys()].sort();
  p.d = ds; p.v = ds.map(d => m.get(d));
  p.last = ds[ds.length - 1];
  return { added, changed };
}

const tail = p => {
  const s = Math.max(0, p.d.length - TAIL_N), out = [];
  for (let i = s; i < p.d.length; i++) out.push([p.d[i], +(+p.v[i]).toFixed(6)]);
  return out;
};

function loadRec() {
  try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { return null; }
}
function applyRec(rec) {
  if (!rec || !rec.items) return 0;
  let n = 0;
  for (const p of D.products) {
    const pts = rec.items[p.id];
    if (!pts || !pts.length) continue;
    if (applyPoints(p, pts)) n++;
  }
  return n;
}
function saveRec(items, tried) {
  try {
    localStorage.setItem(KEY, JSON.stringify({
      ts: Date.now(), updated: bjStamp(), base: D.updated || '', items, tried: tried || {},
    }));
    return true;
  } catch (e) { return false; }
}

/* ------------------------------------------------------------ 基金门控 */
/** 场外基金净值 T 日晚间才披露：盘中期望 T-1（交易日），20:30 后才敢期望 T */
function expectedNavDate(cal, now) {
  if (!cal.length) return null;
  const isTD = cal.indexOf(now.key) >= 0;
  if (isTD && now.hour >= 20.5) return now.key;
  const before = cal.filter(d => d < now.key);
  return before.length ? before[before.length - 1] : null;
}
function calendar() {
  let src = D.products.find(p => p.id === 'SH000300' || p.id === 'SH000510');
  const set = new Set(src ? src.d.slice(-320) : []);
  if (!set.size) for (const p of D.products) if (p.src !== 'fund') p.d.slice(-160).forEach(d => set.add(d));
  return [...set].sort();
}

/* -------------------------------------------------------------- 状态条 */
function setStat(text, kind) {
  const el = $('#lstat'); if (!el) return;
  const map = {
    busy:  'background:#eef3fb;border-color:#c7d8f0;color:#24538f',
    ok:    'background:#f1f8f3;border-color:#c6e4d1;color:#1a7248',
    warn:  'background:#fdf8ec;border-color:#ecd9a8;color:#8a6b1f',
    idle:  'background:#f5f6f8;border-color:#e2e5ea;color:#6a6f7a',
  };
  el.textContent = text;
  el.style.cssText = 'display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:7px;'
    + 'font-size:12px;border:1px solid;white-space:nowrap;line-height:1.5;' + (map[kind] || map.idle);
}
function paintHead(rec) {
  const upd = $('#upd'); if (upd) upd.textContent = rec && rec.updated ? rec.updated : (D.updated || '—');
  const el = $('#fresh'); if (!el) return;
  const ts = rec && rec.ts ? rec.ts : Date.parse((D.updated || '').replace(' ', 'T'));
  const ageH = ts ? (Date.now() - ts) / 3.6e6 : NaN;
  if (!isFinite(ageH)) { el.style.display = 'none'; return; }
  const style = 'margin-top:10px;font-size:12.5px;padding:8px 12px;border-radius:8px;line-height:1.6;';
  if (ageH > 96) {
    el.style.cssText = style + 'background:#fdf3f2;border:1px solid #f3cdca;color:#a3312a';
    el.innerHTML = `⚠ 数据已 <b>${Math.floor(ageH / 24)} 天</b>未更新（最近一次 ${bjStamp(ts)}）。`
      + `可在上方点「立即更新」，或执行 <code>python update_data.py</code> 重建全历史。`;
  } else if (ageH > 30) {
    el.style.cssText = style + 'background:#fdf8ec;border:1px solid #ecd9a8;color:#8a6b1f';
    el.innerHTML = `⚠ 最近成功更新 ${bjStamp(ts)}（${Math.round(ageH)} 小时前）。打开页面会自动尝试补齐。`;
  } else {
    el.style.cssText = style + 'background:#f1f8f3;border:1px solid #c6e4d1;color:#1a7248';
    el.innerHTML = `✓ 数据新鲜度良好，最近成功更新 ${bjStamp(ts)}`
      + `（${ageH < 1 ? Math.round(ageH * 60) + ' 分钟前' : Math.round(ageH) + ' 小时前'}）`;
  }
}

/* ------------------------------------------------------------ 主刷新流程 */
let running = false;

async function refresh(force) {
  if (running) return;
  running = true;
  const btn = $('#btnRefresh');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ 更新中'; }

  const plan = D.products.map(p => ({ p, c: channelOf(p) }));
  const txList = plan.filter(x => x.c.ch === 'tx');
  const fxList = plan.filter(x => x.c.ch === 'fr');
  const pzList = plan.filter(x => x.c.ch === 'pz');
  const now = bjParts();
  const errs = [];
  let done = 0, touched = 0;
  const total = plan.length;
  // 浏览器端无法在线补全的标的（如人民币金价走雅虎，受 CORS 限制）直接跳过，不计入未完成
  const skipList = plan.filter(x => !x.c.ch || x.c.ch === 'skip');
  done = skipList.length;
  const tick = () => setStat(`⟳ 更新中 ${done}/${total}`, 'busy');

  try {
    tick();

    /* ① 腾讯日K */
    await pool(txList, TX_CONC, async ({ p, c }) => {
      const rows = txRows(await txGet(c.id, needBars(p, now.key), c.fq));
      const r = applyPoints(p, rows);
      done++; tick();
      if (!r) errs.push(p.name + '(间隔过久，需跑脚本)');
      else if (r.added || r.changed) touched++;
    });

    /* ② 汇率：欧洲央行日更参考汇率，天然是「日频收盘」，可直接拼接 */
    for (const { p, c } of fxList) {
      try {
        const rows = await frRange(c.id, p.d[p.d.length - 1]);
        const res = applyPoints(p, rows);
        if (res && (res.added || res.changed)) touched++;
        else if (!res) errs.push(p.name + '(无重叠)');
      } catch (e) { errs.push(p.name); }
      done++; tick();
    }

    /* ③ 基金：只有净值确实落后于期望披露日才拉（pingzhongdata 体积大） */
  const exp = expectedNavDate(calendar(), now);
  const tried = (loadRec() || {}).tried || {};
  // QDII 净值天然滞后 T+2，若当天已经查过一次且没查到，就不再重复下载
  const needFunds = pzList.filter(x =>
    (!exp || (x.p.d[x.p.d.length - 1] || '') < exp) && tried[x.p.id] !== now.key);
  const newTried = Object.assign({}, tried);
  for (const x of needFunds) newTried[x.p.id] = now.key;
  done += (pzList.length - needFunds.length); tick();
  if (needFunds.length) {
    await pool(needFunds, PZ_CONC, async ({ p, c }) => {
      const rows = await pzGet(c.id);
      const r = applyPoints(p, rows);
      done++; tick();
      if (!r) errs.push(p.name + '(无重叠)');
      else if (r.added || r.changed) touched++;
      await new Promise(s => setTimeout(s, 120));   // 串行且放慢，避免被限流
    });
  }

  const items = {};
  for (const p of D.products) items[p.id] = tail(p);
  const saved = saveRec(items, newTried);
    const rec = saved ? loadRec() : null;
    paintHead(rec);
    const t = bjStamp();
    if (errs.length)
      setStat(`⟳ 已更新 ${t} · ${touched} 个有变动 · ${errs.length} 项失败`, 'warn');
    else if (touched)
      setStat(`⟳ 已更新 ${t} · ${touched} 个标的有新数据`, 'ok');
    else
      setStat(`✓ ${t} 检查完毕，数据已是最新`, 'ok');
    window.renderAll && window.renderAll();
    fetchRealtime();                       // 主曲线刷新后，同步拉一次实时角标
  } catch (e) {
    setStat('联网失败，显示本地数据', 'warn');
  } finally {
    running = false;
    if (btn) { btn.disabled = false; btn.textContent = '↻ 立即更新'; }
  }
}

function shouldScan(rec) {
  if (running || navigator.onLine === false || document.hidden) return false;
  if (!rec || !rec.ts) return true;
  return Date.now() - rec.ts > SCAN_GAP;
}

/* ------------------------------------------------------------------ 启动 */
function boot() {
  D = window.PORTFOLIO_DATA;
  if (!D || !D.products) return;
  const btn = $('#btnRefresh');
  if (btn) btn.onclick = () => refresh(true);

  const rec = loadRec();
  if (rec && rec.items) {
    if (rec.base && D.updated && rec.base !== D.updated) {
      // 基座被重新生成过：仍尝试合并（applyPoints 会用重叠区自动对齐），
      // 实在对不上会被拒绝写入。
    }
    applyRec(rec);
    paintHead(rec);
    window.renderAll && window.renderAll();
    const age = Date.now() - (rec.ts || 0);
    setStat(age < SCAN_GAP ? `✓ 今日已在 ${rec.updated} 自动更新` : '待更新', age < SCAN_GAP ? 'idle' : 'warn');
  } else {
    paintHead(null);
    setStat('尚未本机刷新过', 'idle');
  }

  if (shouldScan(rec)) setTimeout(() => refresh(false), 500);
  setInterval(() => { if (shouldScan(loadRec())) refresh(false); }, 30 * 60 * 1000);

  // 实时价角标：打开即拉一次；每 30s 刷新（A股/基金仅交易时段有意义，商品/汇率为 24h×工作日随时刷）
  const hasSpot = D.products.some(p => p.src === 'goldcny' || p.src === 'fx');
  fetchRealtime();
  setInterval(() => {
    if (running || navigator.onLine === false || document.hidden) return;
    if (inTradingNow() || hasSpot) fetchRealtime();
  }, 30000);
}
window.__liveBoot = boot;

})();
