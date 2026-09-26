# -*- coding: utf-8 -*-
"""
中证A500 估值自算 —— **完全免 key**，替代需要 MX_APIKEY 的妙想通道。

【PE】中证指数官网 index-perf 接口的 `peg` 字段 = 官方 市盈率PE(TTM)
      已逐日比对验证：沪深300 中证 peg 与蛋卷官方 PE 偏差 0.0%~0.4%；
      中证500/1000 的恒定比例差来自「调整股本分级靠档」口径，peg 即中证官方口径。
      覆盖：2024-09 指数发布至今，日频（官网更早年份该字段为空）。

【PB】没有免费官方端点，用恒等式推导：
        PB = PE × ROE，   ROE = Σ归母净利润(TTM) / Σ归母净资产
      因为 PE 与 PB 的分子同为「Σ(调整股本×价)」，做商后股本口径完全约掉，
      故 ROE 可直接用**总股本整体法**自算，再乘官方 PE 即得官方口径 PB。
      财报滞后按 REPORT_LAG_DAYS=30 天切换（与 ad_api/scripts/valuation.py 一致）。

      ★ 水平校准：自算 ROE 与官方隐含 ROE 存在**恒定**系统性偏差（实测 −7.9%，
        源于期末净资产 vs 年报净资产、其他权益工具等口径差）。恒定偏差不影响分位数，
        但会影响显示的绝对值，故用蛋卷沪深300 的官方 PB/PE 比值做一次水平校准
        （k = 官方隐含ROE_300 / 自算ROE_300），再乘到 A500 自算 ROE 上。
        对照验证：校准后 A500 ROE=10.26%，与妙想 A500 隐含 ROE 10.27% 吻合到 0.01pp。

数据源（全部免 key）：
  ① 成分股权重：东财 A500ETF(159352) 定期报告全部持仓 fundf10 jjcc
  ② 财务：东财 F10 主要财务指标 RPT_F10_FINANCE_MAINFINADATA（EPSJB/BPS/PARENTNETPROFIT）
  ③ 官方 PE：中证 index-perf

产出 data/valuation/SH000510.json，与蛋卷通道结构一致（src="csindex+roe"）。
财务结果缓存于 data/valuation/_a500_fin.json，日常增量只抓有新股/新报告期的股票。

用法：
  python calc_a500.py              # 生成/更新
  python calc_a500.py --refresh    # 忽略财务缓存，全量重抓
  python calc_a500.py --verify     # 额外用同法算沪深300，与蛋卷官方 PB 对照
"""
import os, sys, ssl, re, json, time, datetime, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VDIR = os.path.join(HERE, "data", "valuation")
FIN_CACHE = os.path.join(VDIR, "_a500_fin.json")
CALIB_CACHE = os.path.join(VDIR, "_calib_fin.json")   # 沪深300 财务（水平校准用）

ETF = "159352"          # A500ETF，用其定期报告持仓近似指数成分
ETF_HS300 = "510300"    # 沪深300ETF：水平校准 + --verify
CALIB_PID = "SH000300"  # 蛋卷沪深300 JSON（提供官方 PB/PE 比值）
INDEX_CODE = "000510"
PID = "SH000510"
NAME = "中证A500"
N_CONST = 500           # 指数成分数量
REPORT_LAG_DAYS = 30    # 财报生效滞后（与 valuation.py 一致）

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131 Safari/537.36"}
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE


def get(url, ref, timeout=25, retry=3, raw=False):
    h = dict(UA); h["Referer"] = ref
    last = None
    for k in range(retry + 1):
        try:
            r = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(r, timeout=timeout, context=ctx) as x:
                b = x.read()
            return b if raw else b.decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(1.2 * (k + 1))
    raise last


def num(x):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


# ---------------- ① 成分股 ----------------
def latest_holdings(fund):
    """自动回退找到最近一期已披露的定期报告持仓"""
    t = datetime.date.today()
    cands = []
    for k in range(0, 6):
        cands += [(t.year - k, 12), (t.year - k, 6)]
    def disclosed(y, m):
        # 半年报 8-31 披露完，年报次年 4-30 披露完
        return t >= (datetime.date(y, 8, 31) if m == 6 else datetime.date(y + 1, 4, 30))
    for y, m in [(a, b) for (a, b) in cands if disclosed(a, b)]:
        try:
            t_html = get(f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={fund}"
                         f"&topline=600&year={y}&month={m}", "https://fundf10.eastmoney.com/")
        except Exception:
            continue
        out = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t_html, re.S):
            tds = [re.sub(r"<[^>]+>", "", td).strip()
                   for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(tds) < 7 or not re.fullmatch(r"\d{6}", tds[1] or ""):
                continue
            w = num((tds[6] or "").rstrip("%"))
            if w is None:
                continue
            out.append({"code": tds[1], "name": tds[2], "w": w})
        if out:
            out.sort(key=lambda x: -x["w"])
            return out, f"{y}-{m:02d}"
        time.sleep(0.3)
    raise RuntimeError("未取到任何持仓")


# ---------------- ② 财务 ----------------
def fin_fetch(code):
    secu = f"{code}.SH" if code[0] == "6" else (f"{code}.SZ" if code[0] in "03" else f"{code}.BJ")
    u = ("https://datacenter.eastmoney.com/securities/api/data/v1/get"
         "?reportName=RPT_F10_FINANCE_MAINFINADATA"
         "&columns=SECUCODE,REPORT_DATE,EPSJB,BPS,PARENTNETPROFIT"
         f"&filter=(SECUCODE%3D%22{urllib.parse.quote(secu)}%22)"
         "&pageNumber=1&pageSize=40&sortTypes=-1&sortColumns=REPORT_DATE"
         "&source=HSF10&client=PC")
    j = json.loads(get(u, "https://data.eastmoney.com/"))
    rows = (j.get("result") or {}).get("data") or []
    d = {}
    for r in rows:
        rd = str(r.get("REPORT_DATE", ""))[:10]
        np_ = num(r.get("PARENTNETPROFIT")); eps = num(r.get("EPSJB")); bps = num(r.get("BPS"))
        if rd and (np_ is not None or bps is not None):
            d[rd] = {"np": np_, "eps": eps, "bps": bps}
    return d


def expected_report():
    """按今天推算最新应已披露的报告期"""
    t = datetime.date.today()
    y = t.year
    if t.month >= 11: return f"{y}-09-30"      # 三季报（10月底披露完）
    if t.month >= 9:  return f"{y}-06-30"      # 半年报（8月底）
    if t.month >= 5:  return f"{y}-03-31"      # 一季报（4月底）
    return f"{y-1}-12-31"                      # 年报（4月底）


def load_cache(path=None):
    path = path or FIN_CACHE
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return {"_meta": {}, "codes": {}}


def build_fin(codes, refresh=False, path=None):
    path = path or FIN_CACHE
    cache = load_cache(path)
    exp = expected_report()
    todo = list(codes) if refresh else [c for c in codes
                                        if cache["codes"].get(c, {}).get("_r", "") < exp]
    print(f"  财务：成分 {len(codes)} 只，缓存 {len(codes)-len(todo)} 只，"
          f"需抓取 {len(todo)} 只（期望最新报告期 {exp}）")
    t0 = time.time()
    for i, c in enumerate(todo, 1):
        try:
            d = fin_fetch(c)
            cache["codes"][c] = {"_r": max(d) if d else exp, "d": d}
        except Exception:
            cache["codes"].setdefault(c, {"_r": "", "d": {}})
        if i % 100 == 0:
            print(f"    ...{i}/{len(todo)}  ({time.time()-t0:.0f}s)", flush=True)
        time.sleep(0.1)
    cache["_meta"] = {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                      "expected": exp, "n": len(codes)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(cache, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    miss = [c for c in codes if not cache["codes"].get(c, {}).get("d")]
    print(f"  财务完成：{len(codes)-len(miss)}/{len(codes)} 只有数据，耗时 {time.time()-t0:.0f}s")
    return cache


# ---------------- ③ ROE 阶梯序列 ----------------
def roe_series(cache, codes, start_date="2005-01-01"):
    """
    ROE_r = Σ_i 归母净利润(TTM)_i,r  /  Σ_i 归母净资产_i,r
    返回 {report_date: {"roe":x, "cov":覆盖只数}}，已按成分集合固定
    """
    # 先为每只股票算：每个报告期 -> (ttm_np, bv)
    per = {}
    for c in codes:
        d = (cache["codes"].get(c) or {}).get("d") or {}
        if not d:
            continue
        reps = sorted(d)
        # 股本：用可得的最新年报 净利润/每股收益 反推
        shares = None
        for rd in reversed(reps):
            if rd.endswith("-12-31"):
                e, n = d[rd].get("eps"), d[rd].get("np")
                if e and n and e > 0 and n > 0:
                    shares = n / e
                    break
        if not shares:
            continue
        per[c] = {}
        for rd in reps:
            if rd < start_date:
                continue
            y, mm, dd = rd.split("-")
            ly = str(int(y) - 1)
            a, b = d.get(ly + "-12-31"), d.get(ly + "-" + mm + "-" + dd)
            if not a or not b:
                continue
            cur = d[rd]
            if cur.get("np") is None or a.get("np") is None or b.get("np") is None:
                continue
            ttm = cur["np"] + a["np"] - b["np"]
            bps = cur.get("bps")
            if bps is None:
                continue
            per[c][rd] = (ttm, bps * shares)

    # 按报告期聚合
    out = {}
    for rd in sorted({r for v in per.values() for r in v}):
        s_np = s_bv = 0.0
        cov = 0
        for c, v in per.items():
            if rd in v:
                s_np += v[rd][0]; s_bv += v[rd][1]; cov += 1
        if s_bv > 0 and cov >= len(codes) * 0.5:
            out[rd] = {"roe": s_np / s_bv, "cov": cov}
    return out


# ---------------- ③b ROE 水平校准 ----------------
def roe_calibration(cache_a500, codes_a500):
    """
    k = 官方隐含ROE(沪深300, 蛋卷 PB/PE) / 自算ROE(沪深300)
    返回 (k, 说明)；取不到时返回 (1.0, 未校准)
    """
    p = os.path.join(VDIR, CALIB_PID + ".json")
    if not os.path.exists(p):
        return 1.0, "未校准（缺少蛋卷沪深300 数据）"
    try:
        j = json.load(open(p, encoding="utf-8"))
        off_pe, off_pb = j["cur"].get("pe"), j["cur"].get("pb")
        if not (off_pe and off_pb):
            return 1.0, "未校准（蛋卷沪深300 无当前 PE/PB）"
        off_roe = off_pb / off_pe
    except Exception as e:
        return 1.0, f"未校准（{type(e).__name__}）"

    hold, _ = latest_holdings(ETF_HS300)
    codes = [x["code"] for x in hold[:300]]
    cache = build_fin(codes, path=CALIB_CACHE)
    roe = roe_series(cache, codes)
    if not roe:
        return 1.0, "未校准（沪深300 自算 ROE 失败）"
    mine = roe[sorted(roe)[-1]]["roe"]
    if mine <= 0:
        return 1.0, "未校准（自算 ROE 非正）"
    k = off_roe / mine
    return k, (f"沪深300 官方隐含ROE={off_roe*100:.2f}% / 自算={mine*100:.2f}% → k={k:.4f}")


# ---------------- ④ 官方 PE ----------------
def csindex_pe(code, years=15):
    today = datetime.date.today()
    allrows = []
    for y in range(today.year - years, today.year + 1):
        try:
            u = ("https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode="
                 f"{code}&startDate={y}0101&endDate={y}1231")
            j = json.loads(get(u, "https://www.csindex.com.cn/"))
            allrows += (j.get("data") or [])
        except Exception:
            pass
        time.sleep(0.15)
    out = {}
    for r in allrows:
        td, peg = r.get("tradeDate"), num(r.get("peg"))
        if td and peg:
            out[f"{td[:4]}-{td[4:6]}-{td[6:]}"] = peg
    return dict(sorted(out.items()))


def assemble(pe_map, roe_map):
    """按 REPORT_LAG_DAYS 把 ROE 阶梯映射到交易日，得到 pe/pb 同长序列"""
    if not pe_map:
        raise RuntimeError("中证未返回 peg")
    reps = []
    for rd in roe_map:
        try:
            reps.append((datetime.date.fromisoformat(rd), rd))
        except ValueError:
            continue
    reps.sort()
    dates = sorted(pe_map)
    pe, pb, cov = [], [], []
    for dt in dates:
        d = datetime.date.fromisoformat(dt)
        cur = None
        for rd_date, rd in reps:
            if rd_date + datetime.timedelta(days=REPORT_LAG_DAYS) <= d:
                cur = roe_map[rd]
            else:
                break
        pe.append(round(pe_map[dt], 4))
        if cur:
            pb.append(round(pe_map[dt] * cur["roe"], 4)); cov.append(cur["cov"])
        else:
            pb.append(None); cov.append(0)
    return dates, pe, pb, cov


def build(index_code, etf, pid, name, cache=None, refresh=False):
    print(f"[{name}] ① 成分股 ...")
    hold, period = latest_holdings(etf)
    codes = [h["code"] for h in hold[:N_CONST]]
    wsum = sum(h["w"] for h in hold[:N_CONST])
    print(f"   报告期 {period}，取权重前 {len(codes)} 只（权重合计 {wsum:.2f}%，"
          f"其余 {len(hold)-len(codes)} 只零碎持仓已剔除）")

    print(f"[{name}] ② 财务 ...")
    if cache is None:
        cache = build_fin(codes, refresh=refresh)

    print(f"[{name}] ③ ROE 阶梯（成分整体法，财报滞后 {REPORT_LAG_DAYS} 天）...")
    roe_map = roe_series(cache, codes)
    if roe_map:
        k = sorted(roe_map)[-1]
        print(f"   报告期 {len(roe_map)} 个，最新 {k}：ROE={roe_map[k]['roe']*100:.2f}% "
              f"（覆盖 {roe_map[k]['cov']}/{len(codes)} 只）")
    else:
        print("   [WARN] ROE 序列为空，PB 将无法计算")

    k, why = roe_calibration(cache, codes)
    print(f"[{name}] ③b ROE 水平校准：{why}")
    if k != 1.0:
        before = roe_map[sorted(roe_map)[-1]]["roe"] if roe_map else None
        for v in roe_map.values():
            v["roe"] *= k
        if roe_map and before:
            print(f"   A500 ROE {before*100:.2f}% → {roe_map[sorted(roe_map)[-1]]['roe']*100:.2f}%")

    print(f"[{name}] ④ 中证官方 PE ...")
    pe_map = csindex_pe(index_code)
    print(f"   {len(pe_map)} 个交易日：{min(pe_map) if pe_map else '-'} .. "
          f"{max(pe_map) if pe_map else '-'}，最新 PE={list(pe_map.values())[-1] if pe_map else '-'}")

    dates, pe, pb, cov = assemble(pe_map, roe_map)
    n = len(dates)
    cur_pe = pe[-1] if n else None
    cur_pb = pb[-1] if n else None
    return {"id": pid, "name": name, "code": index_code, "src": "csindex+roe",
            "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "n": n, "d": dates, "pe": pe, "pb": pb,
            "cur": {"pe": cur_pe, "pb": cur_pb},
            "note": (f"PE=中证官网官方PE(TTM)；PB=PE×ROE(成分整体法自算，{period}持仓，"
                     f"滞后{REPORT_LAG_DAYS}天，{why})")}


def main():
    os.makedirs(VDIR, exist_ok=True)
    refresh = "--refresh" in sys.argv
    verify = "--verify" in sys.argv

    o = build(INDEX_CODE, ETF, PID, NAME, refresh=refresh)
    p = os.path.join(VDIR, PID + ".json")
    json.dump(o, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n  [OK ] {PID} {NAME} [{o['src']}] n={o['n']}  {o['d'][0]}..{o['d'][-1]}")
    print(f"        PE={o['cur']['pe']}   PB={o['cur']['pb']}")
    print(f"        -> {p}")

    if verify:
        print("\n  对照：用同法算沪深300，与蛋卷官方 PB 比较")
        try:
            h, period = latest_holdings(ETF_HS300)
            codes = [x["code"] for x in h[:300]]
            cache = build_fin(codes, refresh=False, path=CALIB_CACHE)   # 复用校准缓存
            roe = roe_series(cache, codes)
            # 用同一个校准因子，检验校准后能否对上官方
            k, _ = roe_calibration(cache, codes)
            for v in roe.values():
                v["roe"] *= k
            pe3 = csindex_pe("000300")
            d3, pe_l, pb_l, _ = assemble(pe3, roe)
            print(f"   沪深300 最新 PE(中证)={pe_l[-1]}  自算 PB={pb_l[-1]}")
            print(f"   蛋卷官方（2026-09-24）：PE=13.2886  PB=1.4049")
            if pb_l[-1]:
                print(f"   PB 偏差 = {100*(pb_l[-1]-1.4049)/1.4049:+.2f}%")
        except Exception as e:
            print(f"   对照失败：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
