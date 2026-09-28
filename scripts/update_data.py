# -*- coding: utf-8 -*-
"""
净值 / 行情更新脚本   update_data.py

用法
  python update_data.py            # 增量更新（推荐，日常跑）
  python update_data.py --full     # 强制全量重拉
  python update_data.py --rebuild  # 不联网，仅从 history/ 重建 data.js / data.json
  python update_data.py 000333 024027   # 只更新指定 id

设计要点（实测踩坑而来）
  1. 增量更新：本地 history/*.json 保存全序列，日常只拉最近一段（beg=末日-10d）并合并，
     请求体极小、几乎不触发限流。距上次全量超过 full_stale_days(默认14天) 时自动全量。
  2. 失败沿用：任何标的抓取失败时沿用本地旧数据，绝不写空、不毁掉已有效果；
     全部失败时直接放弃写文件。退出码 0=全成功 2=部分降级。
  3. 温和限速：并发 3、每请求间随机延迟、指数退避重试。
     东财 push2his 对密集全量请求会返回 RemoteDisconnected。

数据源
  股票/指数/ETF/商品/汇率 : 东方财富 push2his 日K
  开放式基金 / QDII      : 天天基金 pingzhongdata.js

口径
  · 股票·ETF : 前复权收盘价 (fqt=1)
  · 基金     : 红利再投资复权净值
               R[i] = R[i-1] * (1 + (eff[i] + div_eff[i] - eff[i-1]) / eff[i-1]), R[0] = 1.0
               eff[i] = nav[i] * 累积份额折算因子   ← 必须处理「拆分」，否则拆分日假摔
               验证：519195 +671.78%（官方累计口径671%）/ 260101 +2285.76% / 481001 +1681.53%
               ⚠ 不要用 equityReturn 连乘，实测部分基金该字段与单位净值不一致。
  · 时区     : 毫秒时间戳按 Asia/Shanghai 折算

输出
  data.js   window.PORTFOLIO_DATA = {...}   ← HTML 用 <script src> 加载，file:// 下可用
  data.json 同内容纯 JSON，同时作为下次的降级种子
"""
import json
import os
import random
import re
import ssl
import sys
import time
import gzip
import urllib.request
import urllib.parse
import concurrent.futures as futures
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（scripts/ 的上一级）
HIST = os.path.join(HERE, "data", "history")
ASSETS = os.path.join(HERE, "assets")
DATA = os.path.join(HERE, "data")
CN = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

FULL_STALE_DAYS = 14      # 距上次全量超过该天数则自动全量
INCR_BACK_DAYS = 10       # 增量回看天数（冗余用于补漏与复权修正）
MAX_WORKERS = 3
MIN_INTERVAL = 0.35       # 单 worker 请求最小间隔(秒)

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------- HTTP
def http_get(url, referer=None, timeout=30, retries=3):
    """退避要克制：最多 8 秒内判定失败并交给备用通道，而不是死等重试。
       实测东财对密集请求会 RemoteDisconnected，等得越久整体越慢。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9", "Connection": "close",
            })
            if referer:
                req.add_header("Referer", referer)
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(min(8, (1.6 ** i)) + random.random() * 0.5)
    raise RuntimeError(f"{type(last).__name__}: {last}")


_last_call = [0.0]
def throttled_get(url, referer=None):
    gap = time.time() - _last_call[0]
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_call[0] = time.time()
    return http_get(url, referer)


# ---------------------------------------------------------------- 东财日K
def fetch_em_kline(secid, beg="19900101", end="20500101"):
    # ⚠ 必须用后复权(fqt=2)：前复权在长周期会得到负值
    #   （累计现金分红超过当期股价时，向前折算后早期价格为负），
    #   实测美的集团前复权首值 -12.78 -> "成立来"被算成 -778%。后复权单调递增且恒正。
    q = {
        "secid": secid, "klt": "101", "fqt": "2",
        "beg": beg, "end": end,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(q)
    txt = throttled_get(url, referer="https://quote.eastmoney.com/")
    j = json.loads(txt)
    d = j.get("data")
    if not d or not d.get("klines"):
        raise RuntimeError(f"无K线数据 secid={secid}")
    dates, vals = [], []
    for line in d["klines"]:
        c = line.split(",")
        dates.append(c[0])                  # f51 日期
        vals.append(round(float(c[2]), 6))  # f53 收盘
    return dates, vals


# ---------------------------------------------------------------- 腾讯日K（备用）
def em2tx(secid):
    """东财 secid -> 腾讯代码；不支持的类型返回 None"""
    if "." not in secid:
        return None
    mkt, code = secid.split(".", 1)
    if mkt == "0":  return "sz" + code
    if mkt == "1":  return "sh" + code
    if mkt == "116": return "hk" + code.zfill(5)
    return None


def _tx_once(code, end="", count=640):
    """腾讯单段。实测要点：
       · 不传 start/end 时上限 2000 条；传了任一个则单段上限 640 条，取 end 之前最近 count 条
       · 复权基准跨段一致，重叠日收盘价逐日相同，可安全拼接
       · ⚠ 必须用 hfq 后复权，理由见 fetch_em_kline 注释（前复权长周期会得到负价格）
       · ⚠ 端点务必用 newfqkline。老的 fqkline 被限流时会返回 HTTP 501，
         而 newfqkline 是独立限流桶，通常仍然可用——实测两者互不牵连。"""
    p = f"{code},day,,{end},{count},hfq"
    for host in ("https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
                 "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
                 "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"):
        try:
            j = json.loads(throttled_get(host + "?param=" + urllib.parse.quote(p)))
            d = j.get("data")
            if not isinstance(d, dict):
                continue
            kk = d.get(code) or {}
            arr = kk.get("hfqday") or kk.get("qfqday") or kk.get("day") or []
            if arr:
                return arr
        except Exception:
            continue
    return []


def fetch_tx_kline(secid, need_full=True, since=None, max_seg=16):
    code = em2tx(secid)
    if not code:
        raise RuntimeError(f"腾讯通道不支持 secid={secid}")
    seen = {}
    end = ""
    for _ in range(max_seg):
        arr = _tx_once(code, end=end)
        if not arr:
            break
        for r in arr:
            seen[r[0]] = (r[0], round(float(r[2]), 6))
        oldest = arr[0][0]
        if not need_full:
            break
        if since and oldest <= since:
            break
        if len(arr) < 640:
            break
        end = (datetime.strptime(oldest, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    if not seen:
        raise RuntimeError(f"腾讯无数据 code={code}")
    ds = sorted(seen)
    return ds, [seen[d][1] for d in ds]


# 简单的通道熔断：连续失败达阈值后本轮直接走备用，避免无效重试拉长总时长
class Breaker:
    def __init__(self, limit=3):
        self.limit = limit
        self.n = 0
    def fail(self):
        self.n += 1
    def ok(self):
        self.n = 0
    @property
    def open(self):
        return self.n >= self.limit


EM_BREAKER = Breaker(3)


def fetch_by_channel(chan, secid, need_full, since):
    if chan == "em":
        if need_full or not since:
            beg = "19900101"
        else:
            beg = (datetime.strptime(since, "%Y-%m-%d") - timedelta(days=INCR_BACK_DAYS)).strftime("%Y%m%d")
        return fetch_em_kline(secid, beg=beg), "东财"
    if chan == "tx":
        return fetch_tx_kline(secid, need_full=need_full, since=since), "腾讯"
    if chan == "sina":
        return fetch_sina_us(secid), "新浪"
    if chan == "yahoo":
        return fetch_yahoo(secid), "雅虎"
    if chan == "fr":
        return fetch_frankfurter(secid, need_full, since), "Frankfurter(ECB)"
    raise RuntimeError(f"未知通道 {chan}")


def fetch_series(p, need_full, since):
    """优先 chan，失败依次降级 chan2 / chan3；chan=auto 时展开为 东财→腾讯。
       每个标的独立选best通 道，避免单点源故障导致整体缺失。"""
    chain = [(p.get("chan", "auto"), p["secid"])]
    for k in ("2", "3"):
        if p.get("chan" + k) and p.get("secid" + k):
            chain.append((p["chan" + k], p["secid" + k]))
    expanded = []
    for c, s in chain:
        expanded += [("em", s), ("tx", s)] if c == "auto" else [(c, s)]

    errs = []
    for c, s in expanded:
        if c == "em" and EM_BREAKER.open:
            errs.append("东财已熔断")
            continue
        try:
            out = fetch_by_channel(c, s, need_full, since)
            if c == "em":
                EM_BREAKER.ok()
            return out
        except Exception as e:
            if c == "em":
                EM_BREAKER.fail()
                if EM_BREAKER.open:
                    print("  [熔断] 东财通道连续失败，本轮其余标的跳过东财", flush=True)
            errs.append(f"{c}({s}):{str(e)[:70]}")
    raise RuntimeError(" | ".join(errs))


def fetch_frankfurter(pair, need_full=True, since=None):
    """Frankfurter / 欧洲央行每日参考汇率
       实测：ACAO=*、无需 key、1999 年起日更，且交易日历覆盖优于雅虎
       （雅虎常缺欧洲假期以外的一些日子）。USD-CNY -> 从 USD 到 CNY。"""
    base, quote = pair.split("-")
    end_s = (datetime.now(CN) + timedelta(days=1)).strftime("%Y-%m-%d")
    if need_full or not since:
        beg_s = "1999-01-04"
    else:
        beg_s = (datetime.strptime(since, "%Y-%m-%d") - timedelta(days=INCR_BACK_DAYS)).strftime("%Y-%m-%d")
    # ⚠ 必须用 api.frankfurter.dev：旧域名 api.frankfurter.app 会 301 到这里，
    #   而 301 响应本身不带 ACAO，浏览器端会被 CORS 拦死（Python 能跟着跳，浏览器不能）。
    j = None
    for host in ("https://api.frankfurter.dev/v1", "https://api.frankfurter.app"):
        try:
            j = json.loads(throttled_get(f"{host}/{beg_s}..{end_s}?from={base}&to={quote}"))
            break
        except Exception:
            continue
    if j is None:
        raise RuntimeError(f"frankfurter 请求失败 pair={pair}")
    rates = j.get("rates") or {}
    dates, vals = [], []
    for d in sorted(rates):
        v = rates[d].get(quote)
        if v:
            dates.append(d); vals.append(round(float(v), 6))
    if not dates:
        raise RuntimeError(f"frankfurter 无数据 pair={pair}")
    return dates, vals


def fetch_sina_us(symbol):
    """新浪美股指数 (.INX 标普500 / .NDX 纳指100 / .DJI 道指 / .IXIC 纳指综合)
       返回体前置有一段 js 注释，取首个 '[' 到末个 ']' 解析即可。"""
    u = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20t_/"
         f"US_MinKService.getDailyK?symbol={symbol}&___qn=3")
    txt = throttled_get(u, referer="https://stock.finance.sina.com.cn/")
    i, j = txt.find("["), txt.rfind("]")
    if i < 0 or j <= i:
        raise RuntimeError(f"新浪返回无法解析 symbol={symbol}")
    arr = json.loads(txt[i:j+1])
    dates, vals = [], []
    for r in arr:
        if not r.get("d") or r.get("c") in (None, "", "0", 0):
            continue
        dates.append(r["d"])
        vals.append(round(float(r["c"]), 6))
    if not dates:
        raise RuntimeError(f"新浪无数据 symbol={symbol}")
    return dates, vals


def fetch_yahoo(symbol, years=25):
    """雅虎财经：USDCNY=X 美元人民币 / DX-Y.NYB 美元指数 / GC=F COMEX黄金
       时间戳按 UTC 折算日期即为交易日。"""
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={years}y&interval=1d"
    # 雅虎偶发 SSL UNEXPECTED_EOF（多与链路中间件有关），就地重试几次
    last = None
    for i in range(3):
        try:
            j = json.loads(throttled_get(u))
            break
        except Exception as e:
            last = e
            j = None
            time.sleep(0.8 + random.random())
    if j is None:
        raise RuntimeError(f"雅虎请求失败 symbol={symbol}: {type(last).__name__}")
    r = (j.get("chart") or {}).get("result") or []
    if not r:
        raise RuntimeError(f"雅虎无数据 symbol={symbol}")
    r = r[0]
    ts = r.get("timestamp") or []
    cl = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    dates, vals = [], []
    for t, c in zip(ts, cl):
        if c is None:
            continue
        dates.append(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"))
        vals.append(round(float(c), 6))
    if not dates:
        raise RuntimeError(f"雅虎无有效收盘 symbol={symbol}")
    return dates, vals


# ---------------------------------------------------------------- 人民币金价（COMEX × 汇率）
def fetch_gold_cny(p, need_full, since):
    """人民币金价（元/克）= COMEX 黄金(GC=F, 美元/盎司) × 美元兑人民币 ÷ 31.1034768

        · GC=F 走雅虎（与 fetch_yahoo 同口径，无需 key）
        · USD-CNY 走 Frankfurter(ECB)，与汇率标的同源
        · 两条通道均免费、无 CORS 限制，适合 Actions 每日重抓
        · 浏览器端 live.js 无雅虎通道（CORS），故该标的仅由每日批处理刷新，不强制在线补全
        返回 (dates, vals)，vals 单位为 元/克。"""
    if need_full or not since:
        gd, gv = fetch_yahoo("GC=F", years=25)
    else:
        yrs = max(3, int((datetime.now(CN) - datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=CN)).days / 365) + 1)
        gd, gv = fetch_yahoo("GC=F", years=yrs)
    cd, cv = fetch_frankfurter("USD-CNY", need_full, since)
    cny = {d: v for d, v in zip(cd, cv)}

    from datetime import timedelta
    def asdate(s):
        return datetime.strptime(s, "%Y-%m-%d")

    out_d, out_v = [], []
    for d, g in zip(gd, gv):
        if not g or g <= 0:
            continue
        rate = None
        for back in range(0, 8):                      # 回退至多 7 个日历日找最近一个汇率日
            dd = (asdate(d) - timedelta(days=back)).strftime("%Y-%m-%d")
            if dd in cny:
                rate = cny[dd]
                break
        if rate and rate > 0:
            out_v.append(round(g * rate / 31.1034768, 4))
            out_d.append(d)
    if not out_d:
        raise RuntimeError("黄金折算无可用数据（GC=F 或 USD-CNY 缺失）")
    return out_d, out_v


# ---------------------------------------------------------------- 中债国债收益率曲线
CN_GOV_YC_ID = "2c9081e50a2f9606010a3068cae70001"   # 中债国债收益率曲线(到期)
YIELD_BATCH = 6          # 接口单次最多 6 个日期
YIELD_WORKERS = 4        # 批次并行度（中债对并发敏感，太高会超时）
YIELD_YEARS = 5          # 全量时的历史长度（年）

# ⚠ 必须绕过本机代理：中债走 HTTP_PROXY 会 502 Bad Gateway（Tunnel connection failed）。
#   国内站点直连更稳，这里建一个不含代理的 opener。
_NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_ctx))


def _cn_yield_batch(days, retries=3):
    """查一批（≤6 个日期）国债收益率曲线，返回 {日期: {期限年: 收益率%}}。
       实测要点：必须 POST（GET 返回 405），参数全在 querystring，表单体为空；
       期限网格里 1 / 3 / 10 年是精确节点，直接按浮点等值取。

       ⚠ 容错优先：中债偶发超时（WinError 10060）/ 502，单批失败只放弃这一批
       （返回空字典），绝不让个别批次拖垮整个标的——少几个交易日远好过整体缺失。"""
    qs = ("xyzSelect=txy&&workTimes=%s&&dxbj=0&&qxll=0,&&yqqxN=N&&yqqxK=K"
          "&&ycDefIds=%s&&locale=zh_CN" % (",".join(days), CN_GOV_YC_ID))
    url = "https://yield.chinabond.com.cn/cbweb-mn/yc/searchYc?" + qs
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={
        "User-Agent": UA,
        "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "close",
    })
    arr = None
    for attempt in range(retries):
        try:
            with _NO_PROXY_OPENER.open(req, timeout=40) as r:
                arr = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception:
            if attempt >= retries - 1:
                return {}                      # 该批放弃，交给 merge 与其它批次的结果
            time.sleep(0.6 * (attempt + 1) + random.random() * 0.4)
    if arr is None:
        return {}
    out = {}
    for it in (arr or []):
        d = it.get("worktime")
        if not d:
            continue
        hit = {}
        for t, y in (it.get("seriesData") or []):
            for k in (1, 3, 10):
                if abs(float(t) - k) < 1e-6:
                    hit[k] = float(y)
        if len(hit) == 3:
            out[d] = hit
    return out


def fetch_cn_gov_yield(need_full=True, since=None, years=YIELD_YEARS):
    """中债国债收益率曲线 1Y/3Y/10Y 历史，单位 %（利率水平，不是涨跌幅）。

       · 权威源：中国债券信息网（中债估值），T+1 发布
       · 接口限制：单次最多 6 个日期 → 按 6 天一批分页，批次间并行
       · 只需传工作日（周一~周五），无估值的日期接口自然不返回"""
    end = datetime.now(CN)
    if need_full or not since:
        start = end - timedelta(days=int(365 * years))
    else:
        # ⚠ 必须补 tzinfo：end 是 aware（CN），naive 与 aware 相减/比较会抛
        #   "can't compare offset-naive and offset-aware datetimes"
        start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=CN) - timedelta(days=INCR_BACK_DAYS)
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:              # 0=周一 ... 4=周五
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    batches = [days[i:i + YIELD_BATCH] for i in range(0, len(days), YIELD_BATCH)]

    got = {}
    with futures.ThreadPoolExecutor(max_workers=YIELD_WORKERS) as ex:
        for out in ex.map(_cn_yield_batch, batches):
            got.update(out)
    if not got:
        raise RuntimeError("中债国债收益率曲线无数据")
    ds = sorted(got)
    return ds, {"1Y": [got[d][1] for d in ds],
                "3Y": [got[d][3] for d in ds],
                "10Y": [got[d][10] for d in ds]}


def merge_multi(old, dates, ser):
    """多序列增量合并（以日期为键，新值覆盖旧值）"""
    m = {}
    if old.get("y1"):
        for d, a, b, c in zip(old["d"], old["y1"], old["y3"], old["y10"]):
            m[d] = (a, b, c)
    for i, d in enumerate(dates):
        m[d] = (ser["1Y"][i], ser["3Y"][i], ser["10Y"][i])
    ds = sorted(m)
    return ds, {"1Y": [m[d][0] for d in ds],
                "3Y": [m[d][1] for d in ds],
                "10Y": [m[d][2] for d in ds]}


# ---------------------------------------------------------------- 基金复权净值
def parse_unit_money(unit_money):
    """
      '分红：每份派现金0.05元'            -> (0.05, 1.0)
      '拆分：每份基金份额折算3.630506051份' -> (0.0, 3.630506051)
    """
    if not unit_money:
        return 0.0, 1.0
    s = str(unit_money)
    div, ratio = 0.0, 1.0
    m = re.search(r"派现金\s*([0-9]*\.?[0-9]+)\s*元", s)
    if m:
        try: div = float(m.group(1))
        except ValueError: pass
    m = re.search(r"折算成?\s*([0-9]*\.?[0-9]+)\s*份", s)
    if m:
        try:
            r = float(m.group(1))
            if r > 0: ratio = r
        except ValueError: pass
    return div, ratio


def _grab(txt, varname):
    i = txt.find(varname)
    if i < 0:
        raise RuntimeError(f"未找到 {varname}")
    j = txt.find("=", i) + 1
    depth, k = 0, j
    while k < len(txt):
        ch = txt[k]
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ";" and depth <= 0:
            break
        k += 1
    return txt[j:k].strip().rstrip(";").strip()


def fix_splits(vals, drop=-0.40, jump=1.50):
    """份额折算兜底校正

    pingzhongdata 的 unitMoney（份额折算）字段偶有缺失，东财日K后复权偶会漏处理 ETF 份额折算，
    结果是净值在折算日出现「假瀑布」——例如 561380 在 2026-06-24 由 2.3786 折到 0.9488(-60.11%)，
    与电网设备指数同期走势完全背离，相关性被算成 0.41（校正后 0.96）。

    判据：净值型产品单日不可能跌超 40% 或涨超 150%，出现即为折算/拆分。
    处理：把该日之前的全部点位按同一比例缩放，让折算前后回到同一份额基准。
    ⚠ 只在全量序列上调用——增量窗口只有十余天，窗口内无跳变不代表历史干净。"""
    n = len(vals)
    if n < 3:
        return vals, 0
    out = list(vals)
    fixes = 0
    for i in range(1, n):
        a, b = out[i - 1], out[i]
        if not a or not b or a <= 0 or b <= 0:
            continue
        chg = b / a - 1.0
        if chg <= drop or chg >= jump:
            k = b / a
            for j in range(i):
                out[j] = round(out[j] * k, 6)
            fixes += 1
    return out, fixes


def fetch_fund_nav(code):
    txt = throttled_get(f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
                        referer="https://fund.eastmoney.com/")
    trend = json.loads(_grab(txt, "Data_netWorthTrend"))
    if not trend:
        raise RuntimeError(f"Data_netWorthTrend 为空 code={code}")

    dates, navs, divs, splits = [], [], [], []
    for it in trend:
        ts, y = it.get("x"), it.get("y")
        if ts is None or y is None:
            continue
        d_, s_ = parse_unit_money(it.get("unitMoney") or "")
        dates.append(datetime.fromtimestamp(ts / 1000, CN).strftime("%Y-%m-%d"))
        navs.append(float(y)); divs.append(d_); splits.append(s_)

    n = len(navs)
    # 累积份额折算因子 -> 把拆分前后拉回同一份额基准
    cum, eff, div_eff, n_div, n_spl = 1.0, [], [], 0, 0
    for i in range(n):
        cum *= splits[i]
        if splits[i] != 1.0: n_spl += 1
        if divs[i] > 0:      n_div += 1
        eff.append(navs[i] * cum); div_eff.append(divs[i] * cum)

    R, r = [1.0], 1.0
    for i in range(1, n):
        prev = eff[i - 1]
        if prev and prev > 0:
            r = r * (1.0 + (eff[i] + div_eff[i] - prev) / prev)
        R.append(r)
    base = navs[0] if navs else 1.0
    vals = [round(x * base, 6) for x in R]

    warn = ""
    try:
        ac = json.loads(_grab(txt, "Data_ACWorthTrend"))
        if ac and ac[0][1] and ac[-1][1] > 0 and R[-1] > 0:
            years = max((n - 1) / 244.0, 1e-6)
            ac_ratio = ac[-1][1] / ac[0][1]
            if n_div > 0:
                gap = ((R[-1] / ac_ratio) ** (1.0 / years) - 1.0) * 100.0
                warn = f"分红{n_div}次/拆分{n_spl}次；复权较累计净值年化高 {gap:+.2f}pp（含红利再投资收益）"
    except Exception:
        pass
    return dates, vals, warn


# ---------------------------------------------------------------- 本地历史
def hist_path(pid):
    return os.path.join(HIST, f"{pid}.json")


def load_hist(pid):
    p = hist_path(pid)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_hist(pid, obj):
    os.makedirs(HIST, exist_ok=True)
    tmp = hist_path(pid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, hist_path(pid))


def merge(old, dates, vals):
    m = dict(zip(old["d"], old["v"]))
    for d, v in zip(dates, vals):
        m[d] = v
    ds = sorted(m)
    return ds, [m[d] for d in ds]


# ---------------------------------------------------------------- ETF 价-净溢价率
def fetch_etf_premium(code, dates, vals):
    """ETF 价-净溢价率 = (二级市价 - 单位净值) / 单位净值 × 100%。
       单位净值取自天天基金 pingzhongdata 的 Data_netWorthTrend（y 字段，按交易日对齐）。
       仅用于「信息行」展示最新溢价率（非历史曲线），故只回传最新一个非空值与其日期。"""
    import re as _re
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    try:
        raw = throttled_get(url, referer=f"https://fundf10.eastmoney.com/")
    except Exception as e:
        print(f"  [溢价率] {code} 净值抓取失败：{type(e).__name__} {str(e)[:60]}", flush=True)
        return None
    txt = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    m = _re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", txt, _re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return None
    nav = {}
    for it in arr:
        try:
            d = datetime.fromtimestamp(it["x"] / 1000).strftime("%Y-%m-%d")
            nav[d] = float(it["y"])
        except Exception:
            continue
    pnow, pdate = None, ""
    for d, v in zip(dates, vals):
        n = nav.get(d)
        if n and n > 0 and v and v > 0:
            pnow, pdate = round((v - n) / n * 100, 4), d
    if pnow is None:
        return None
    return {"premNow": pnow, "premDate": pdate}


# ---------------------------------------------------------------- 单个标的
def update_one(p, full):
    pid = p["id"]
    old = load_hist(pid)
    need_full = full or old is None
    if not need_full:
        # ⚠ 两边都必须带 tzinfo，否则 aware - naive 会抛 TypeError
        try:
            fulled = datetime.strptime(old.get("fulled") or "1999-01-01", "%Y-%m-%d").replace(tzinfo=CN)
        except Exception:
            fulled = datetime(1999, 1, 1, tzinfo=CN)
        if (datetime.now(CN) - fulled).days > FULL_STALE_DAYS:
            need_full = True

    if need_full:
        beg_since = None
    else:
        beg_since = old.get("last") or (old["d"][-1] if old and old.get("d") else None)

    warn = "",
    extra = {}
    if p["src"] == "fund":
        dates, vals, warn = fetch_fund_nav(p["secid"])
        channel = "天天基金"
    elif p["src"] == "goldcny":
        dates, vals = fetch_gold_cny(p, need_full, beg_since)
        channel = "雅虎×ECB"
    elif p["src"] == "cnyield":
        # 多序列标的：1Y/3Y/10Y 三条曲线共享同一日期轴，画在同一张图里
        dates, ser = fetch_cn_gov_yield(need_full, beg_since)
        if not need_full and old and old.get("y1"):
            dates, ser = merge_multi(old, dates, ser)
        vals = ser["10Y"]                 # 主序列取 10Y，供通用切片/兜底使用
        extra = {"y1": ser["1Y"], "y3": ser["3Y"], "y10": ser["10Y"], "kind": "yield"}
        channel = "中债估值"
    else:
        (dates, vals), channel = fetch_series(p, need_full, beg_since)

    # 份额折算兜底：仅全量序列才做（增量窗口太短，看不出历史跳变）
    if need_full and p["src"] != "cnyield" and len(dates) == len(vals):
        vals, nfix = fix_splits(vals)
        if nfix:
            print(f"  [折算校正] {pid} {p['name']}：检出并还原 {nfix} 处份额折算跳变", flush=True)

    if not need_full and old and p["src"] != "cnyield":
        dates, vals = merge(old, dates, vals)

    # ETF 价-净溢价率（仅场内 ETF 标的、且标记 prem:true 时抓取）
    if p.get("prem") and dates and vals and len(dates) == len(vals):
        pr = fetch_etf_premium(p["id"], dates, vals)
        if pr:
            extra.update(pr)

    today = datetime.now(CN).strftime("%Y-%m-%d")
    obj = {
        "id": pid, "name": p["name"], "code": p["code"], "group": p["group"],
        "src": p["src"], "secid": p["secid"], "unit": p.get("unit", ""),
        "note": p.get("note", ""), "chan": channel,
        "slot": p.get("slot", ""),
        "fulled": today if need_full else old.get("fulled", ""),
        "last": dates[-1] if dates else "",
        "d": dates, "v": vals,
    }
    obj.update(extra)                  # 多序列标的：y1/y3/y10 + kind
    if warn and warn != ("",):
        obj["warn"] = warn
    return pid, obj, None


def update_one_safe(p, full):
    try:
        return update_one(p, full)
    except Exception as e:
        return p["id"], None, f"{p['name']}({p['id']}): {type(e).__name__} {str(e)[:150]}"


# ---------------------------------------------------------------- 主流程
def write_payload(prods, plist, tag="", degraded=None):
    for x in plist:
        x.setdefault("inception", x["d"][0] if x.get("d") else "")
        if x.get("d"):
            x["last"] = x["d"][-1]
    payload = {
        "updated": datetime.now(CN).strftime("%Y-%m-%d %H:%M"),
        "tz": "Asia/Shanghai",
        "source": "东方财富 push2his / 天天基金 pingzhongdata / 腾讯 / 新浪 / 雅虎",
        "note": ("股票·ETF为后复权收盘价；基金为红利再投资复权净值（含分红与份额折算）；"
                 "黄金为COMEX黄金(美元/盎司)×美元兑人民币汇率折算的人民币元/克；汇率为欧洲央行每日参考汇率"),
        "products": plist,
    }
    if degraded:
        payload["degraded"] = degraded
    js = os.path.join(ASSETS, "data.js")
    with open(js, "w", encoding="utf-8") as f:
        f.write("window.PORTFOLIO_DATA = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    with open(os.path.join(DATA, "data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"输出 {js}  ({os.path.getsize(js)/1024:.0f} KB)   更新于 {payload['updated']}{tag}")
    return payload


def rebuild():
    """不联网，仅把 history/ 里的缓存合成 data.js —— 改了 products.json 元信息后用它刷新产物"""
    with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
        prods = json.load(f)["products"]
    plist, miss = [], []
    for p in prods:
        h = load_hist(p["id"])
        if not h:
            miss.append(p["id"])
            continue
        h["name"] = p["name"]; h["code"] = p["code"]; h["group"] = p["group"]
        h["unit"] = p.get("unit", ""); h["note"] = p.get("note", "")
        h["src"] = p.get("src", h.get("src"))
        h["secid"] = p.get("secid", h.get("secid"))
        h["chan"] = h.get("chan") or p.get("chan", "")
        h["chan2"] = p.get("chan2", ""); h["secid2"] = p.get("secid2", "")
        h["kind"] = p.get("kind", "")
        h["slot"] = p.get("slot", "")
        h["pair"] = p.get("pair", "")
        h["prem"] = p.get("prem", False)
        plist.append(h)
    write_payload(prods, plist, tag="  [离线重建]")
    print(f"重建 {len(plist)}/{len(prods)}" + (f"，缺失 {miss}" if miss else ""))
    return 0 if not miss else 2


def main():
    args = [a for a in sys.argv[1:]]
    if "--rebuild" in args:
        return rebuild()
    full = "--full" in args
    args = [a for a in args if a != "--full"]

    with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
        prods = json.load(f)["products"]
    # 用 history 里的未知 id 反查产品定义缺失的情况，保证 restore 时能带上元信息
    if args:
        prods = [p for p in prods if p["id"] in args]

    # 上一轮产物，作为失败时的降级种子
    prev_map = {}
    for fp in (os.path.join(DATA, "data.json"), os.path.join(ASSETS, "data.js")):
        if not os.path.exists(fp):
            continue
        try:
            txt = open(fp, encoding="utf-8").read()
            txt = txt[txt.find("{"):] if fp.endswith(".js") else txt
            txt = txt.rstrip().rstrip(";")
            for pr in json.loads(txt).get("products", []):
                prev_map.setdefault(pr["id"], pr)
        except Exception:
            pass

    os.makedirs(HIST, exist_ok=True)
    # 用已有 history 补全种子（优先）
    for p in prods:
        h = load_hist(p["id"])
        if h:
            prev_map.setdefault(p["id"], h)

    print(f"开始更新 {len(prods)} 个标的  模式={'全量' if full else '增量'}", flush=True)
    results, errors = {}, []
    with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        tasks = [ex.submit(update_one_safe, p, full) for p in prods]
        for fu in futures.as_completed(tasks):
            pid, obj, err = fu.result()
            if err:
                if pid in prev_map:
                    results[pid] = prev_map[pid]
                    errors.append(err)
                    print(f"  [降级] {err}  -> 沿用旧数据", flush=True)
                else:
                    errors.append(err)
                    print(f"  [失败] {err}", flush=True)
            else:
                results[pid] = obj
                save_hist(pid, obj)
                tag = "全量" if obj.get("fulled") == datetime.now(CN).strftime("%Y-%m-%d") else "增量"
                print(f"  [OK/{tag}/{obj.get('chan','')}] {pid:9s} {obj['name']:26s} n={len(obj['d']):5d}  "
                      f"{obj['d'][0]} ~ {obj['d'][-1]}", flush=True)

    if not results:
        print("全部失败且无本地历史，放弃写文件（保留上一版 data.js）")
        return 1

    order = {p["id"]: i for i, p in enumerate(prods)}
    plist = sorted(results.values(), key=lambda x: order.get(x["id"], 999))
    write_payload(prods, plist, degraded=[e.split(":")[0] for e in errors])

    size = os.path.getsize(os.path.join(ASSETS, "data.js")) / 1024
    print("-" * 78)
    print(f"完成 {len(plist)}/{len(prods)}   降级/失败 {len(errors)}   ({size:.0f} KB)")
    if errors:
        print("降级明细（沿用旧数据）:")
        for e in errors:
            print("   ", e)
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
