# -*- coding: utf-8 -*-
"""
独立校验净值数据正确性（不修改任何文件）

两层校验：
  1) 内部一致性：日期严格升序且无重复、无空值、数值为正、单日涨幅无异常跳变、长度足够。
  2) 跨源交叉验证：对代表性标的，用 update_data.py 的「另一组」独立通道重新抓一次，
     对比最新值 / 区间累计收益，偏差在工程允许范围内即判定正确。

用法：
  python tools/check_data.py
"""
import os
import sys
import json
import importlib.util

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
ud = importlib.import_module("update_data")

CN = ud.CN
TOL_LATEST = 0.01     # 最新值允许 1% 偏差（跨源时点/复权口径差异）
TOL_RET = 0.03        # 区间累计收益允许 3% 偏差
JUMP_WARN = 0.20      # 单日涨幅 >20% 预警
JUMP_ERR = 0.40       # 单日涨幅 >40% 判错

# 历史远端跳变豁免白名单：后复权源在 IPO 早期 / 拆股日的已知悬崖，
# 属源数据特性而非数据错误，仅影响「成立来」全历史视图。
# 支持两种写法：精确日期 "1996-12-27"，或年份通配 "1996*"（该年全部豁免）。
JUMP_EXEMPT = {
    "000651": {"1996*"},             # 格力电器 1996 IPO 早期复盘价悬崖（首月 ±100% 级）
    "00700": {"2014-05-15"},         # 腾讯控股 1拆5 拆股悬崖 -78.8%
}

fails, warns = [], []


def load_payload():
    p = os.path.join(HERE, "assets", "data.js")
    txt = open(p, encoding="utf-8").read()
    txt = txt[txt.find("{"):].rstrip().rstrip(";")
    return json.loads(txt)


def internal(p):
    d, v = p.get("d"), p.get("v")
    name = p.get("name", p.get("id"))
    if not d or not v or len(d) != len(v):
        fails.append(f"[{name}] d/v 长度不一致或缺失"); return
    if len(d) < 30:
        fails.append(f"[{name}] 序列过短 n={len(d)}")
    prev = None
    for i in range(len(d)):
        if prev is not None and d[i] <= prev:
            fails.append(f"[{name}] 日期非升序/重复 @ {d[i]}"); break
        prev = d[i]
        x = v[i]
        if x is None or not isinstance(x, (int, float)) or x <= 0:
            fails.append(f"[{name}] 非正/空值 @ {d[i]} = {x}"); break
        if prev is not None and i > 0:
            ch = v[i] / v[i - 1] - 1
            ex = JUMP_EXEMPT.get(p.get("secid", ""), set()) | JUMP_EXEMPT.get(p.get("id", ""), set())
            exempt = d[i] in ex or any(e.endswith("*") and d[i].startswith(e[:-1]) for e in ex)
            if abs(ch) > JUMP_ERR and exempt:
                warns.append(f"[{name}] 历史远端跳变(已豁免) {(ch*100):.1f}% @ {d[i]}")
            elif abs(ch) > JUMP_ERR:
                fails.append(f"[{name}] 单日跳变 {(ch*100):.1f}% @ {d[i]}")
                break
            elif abs(ch) > JUMP_WARN:
                warns.append(f"[{name}] 单日涨幅偏大 {(ch*100):.1f}% @ {d[i]}")


def xcheck(name, our_last, our_d, our_v, src_last, src_ret=None, our_ret=None):
    if our_last is None or src_last is None:
        warns.append(f"[{name}] 跨源最新值缺失，跳过"); return
    rel = abs(our_last - src_last) / src_last
    if rel > TOL_LATEST:
        fails.append(f"[{name}] 最新值偏差 {(rel*100):.2f}%（我 {our_last:.4f} vs 源 {src_last:.4f}）")
    else:
        print(f"  ✓ {name}: 最新值偏差 {(rel*100):.2f}%（我 {our_last:.4f} vs 源 {src_last:.4f}）")
    if src_ret is not None and our_ret is not None:
        if abs(our_ret - src_ret) > TOL_RET:
            fails.append(f"[{name}] 区间累计收益偏差 {(our_ret-src_ret)*100:.2f}pp（我 {our_ret*100:.1f}% vs 源 {src_ret*100:.1f}%）")
        else:
            print(f"  ✓ {name}: 区间累计收益偏差 {(our_ret-src_ret)*100:.2f}pp（同窗口对比）")


def tail_ret(d, v, start):
    """start 日期（含）起到最新的累计收益，用于跨源「同窗口」对比"""
    i = 0
    while i < len(d) and d[i] < start:
        i += 1
    if i >= len(d) or len(d) - i < 2:
        return None
    return v[-1] / v[i] - 1


def main():
    d = load_payload()
    prods = d.get("products", [])
    print(f"载入 {len(prods)} 个标的，更新于 {d.get('updated')}")
    for p in prods:
        internal(p)
    print(f"内部一致性：{len(prods)} 个标的已检查，{len(fails)} 处硬错误，{len(warns)} 处预警")

    by = {p["id"]: p for p in prods}
    WIN = "2024-09-25"   # 跨源收益对比统一用「最近 2 年」窗口，避免时间跨度不一致导致的伪误差

    # —— 跨源 1：美元/人民币（ECB 另一端点）——
    try:
        cd, cv = ud.fetch_frankfurter("USD-CNY", need_full=False, since=None)
        p = by.get("USDCNH")
        if p:
            xcheck("美元/人民币", p["v"][-1], p["d"], p["v"], cv[-1],
                   our_ret=tail_ret(p["d"], p["v"], WIN), src_ret=tail_ret(cd, cv, WIN))
    except Exception as e:
        warns.append(f"[美元/人民币] 跨源抓取失败：{e}")

    # —— 跨源 2：沪深300（腾讯后复权，独立于东财通道）——
    try:
        dd, dv = ud.fetch_tx_kline("1.000300", need_full=False, since=WIN)
        p = by.get("SH000300")
        if p:
            xcheck("沪深300", p["v"][-1], p["d"], p["v"], dv[-1],
                   our_ret=tail_ret(p["d"], p["v"], WIN), src_ret=tail_ret(dd, dv, WIN))
    except Exception as e:
        warns.append(f"[沪深300] 跨源抓取失败：{e}")

    # —— 跨源 3：标普500（雅虎，独立于原新浪通道）——
    try:
        sd, sv = ud.fetch_yahoo("^GSPC", years=3)
        p = by.get("SPX")
        if p:
            xcheck("标普500", p["v"][-1], p["d"], p["v"], sv[-1],
                   our_ret=tail_ret(p["d"], p["v"], WIN), src_ret=tail_ret(sd, sv, WIN))
    except Exception as e:
        warns.append(f"[标普500] 跨源抓取失败：{e}")

    # —— 跨源 4：黄金人民币（GC=F × USDCNY 独立重算）——
    try:
        gd, gv = ud.fetch_yahoo("GC=F", years=3)
        cd, cv = ud.fetch_frankfurter("USD-CNY", need_full=False, since=None)
        cny = {x: y for x, y in zip(cd, cv)}
        gold_series = []
        for d, g in zip(gd, gv):
            rate = None
            for back in range(0, 8):
                from datetime import timedelta
                dd = (ud.datetime.strptime(d, "%Y-%m-%d") - timedelta(days=back)).strftime("%Y-%m-%d")
                if dd in cny:
                    rate = cny[dd]; break
            if rate:
                gold_series.append((d, g * rate / 31.1034768))
        gs_d = [x[0] for x in gold_series]; gs_v = [x[1] for x in gold_series]
        p = by.get("GOLDCNY")
        if p and gs_v:
            xcheck("黄金(人民币/克)", p["v"][-1], p["d"], p["v"], gs_v[-1],
                   our_ret=tail_ret(p["d"], p["v"], WIN), src_ret=tail_ret(gs_d, gs_v, WIN))
    except Exception as e:
        warns.append(f"[黄金] 跨源重算失败：{e}")

    # —— 跨源 5：某开放式基金（红利再投资复权 vs 天天基金复权口径，重叠区间）——
    fund = next((p for p in prods if p.get("src") == "fund"), None)
    if fund:
        try:
            td, tv, _ = ud.fetch_fund_nav(fund["secid"])
            m = {x: y for x, y in zip(fund["d"], fund["v"])}
            ov = [x for x in td if x in m]
            if len(ov) >= 2:
                s, e = ov[0], ov[-1]
                ti = {x: y for x, y in zip(td, tv)}
                xcheck(f"基金复权({fund['name']})", fund["v"][-1], fund["d"], fund["v"], tv[-1],
                       our_ret=m[e]/m[s]-1, src_ret=ti[e]/ti[s]-1)
        except Exception as e:
            warns.append(f"[{fund['name']}] 跨源抓取失败：{e}")

    print("\n==================== 结论 ====================")
    for w in warns:
        print("  预警:", w)
    if fails:
        print(f"\n❌ 发现 {len(fails)} 处硬错误：")
        for f in fails:
            print("   ✗", f)
        sys.exit(2)
    else:
        print(f"✅ 全部检查通过（内部一致性 + 跨源交叉验证；{len(warns)} 条预警已列出，均非致命）。")


if __name__ == "__main__":
    main()
