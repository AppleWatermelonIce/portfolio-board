#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio-board 数据口径自测用例（回归测试 / 离线、不联网）

运行:
    python scripts/test_data_integrity.py
退出码:
    0 = 全部通过
    1 = 存在失败项

============================================================================
回归背景（2026-10-12 发现并修复的系统 bug）
----------------------------------------------------------------------------
update_data.py 的 fetch_fund_nav 原本用天天基金 Data_netWorthTrend 的
unitMoney 字段解析现金分红金额。但 2026 年起该字段不再携带金额
（例: "分红：每10份派现金元" 数字缺失），导致:

    * 分红检测为 0 -> 复权净值 v 退化为单位净值 u
    * 分红日单位净值出现 -3%~-5% 的假瀑布，复权曲线 v 跟着同幅度下跌
    * 典型触发: 006431（分红日 u 跌 -4.67%/-2.94%/-2.41%，v 未还原）

已改用「累计净值(ACWorthTrend) - 单位净值」差额回推每日分红，再红利再投资。
fetch_fund_nav 把"累计分红末点 divCum = ac[-1] - u[-1]"写入 history，作为本自测
判定"该基金是否有现金分红"的**真值**——避免仅凭日跌幅误判（股混基金市场大跌日
u/v 同跌，并非分红未还原）。

============================================================================
覆盖的不变量
----------------------------------------------------------------------------
I1 基金分红还原(真值驱动): 读 history 的 divCum（累计分红末点）。
    * 有分红(divCum > REINV_EPS): 复权净值 v[-1] 必须明显高于单位净值 u[-1]
      （红利再投资使复权末点高于单位净值），且 v[-1]-u[-1] 不得低于
      divCum - 0.02（否则 v 退化为 u 或仅部分还原）-> FAIL。
    * 无分红(divCum ≈ 0): v[-1] 应与 u[-1] 相等（abs < 1e-4）-> 否则 FAIL。
    * 有分红但 v 仍处处等于 u（v[-1]≈u[-1]）即本 bug 的退化形态 -> FAIL。

I2 基金 history 必须含 divCum 字段: 若 src=='fund' 且有 u/v，必须写有 divCum
    （由 update_data.py 生成）；缺字段说明该基金未用修复后的脚本重跑 -> FAIL。

I3 股票/ETF/指数前复权: src=='em' 的品种必须含 q，且 len(q)==len(d)。
    （sina/yahoo/goldcny/fx/cnyield 本就不含 q，不检查，避免误 FAIL。）

I4 债券多序列: src=='cnyield' 的品种必须含 y1/y3/y10。

GOLDEN 已知分红基金复权再投断言: 006431/006670/004102/002065/485119/000369
    必须 divCum > 0 且 v[-1]-u[-1] > 0.01，否则回归。
============================================================================
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "history")
PROD = os.path.join(ROOT, "data", "products.json")

REINV_EPS = 0.002   # divCum / (v[-1]-u[-1]) 超过此值视为有红利再投(有分红)
GOLDEN_EPS = 0.01   # 黄金用例: 有分红基金 v 至少比 u 高 1%

GOLDEN_FUNDS = ["006431", "006670", "004102", "002065", "485119", "000369"]

# 已知限制: 下列 em 源(指数/跨境 ETF)腾讯 qfq 本不覆盖该品种, 降级东财 fqt=1
# 又是按规矩禁用的减法式前复权(长周期会出负值), 故历史无 q 属合规结果;
# 前端「市价」口径对缺 q 的 em 自动回退等比前复权(末点=真实收盘价)。不入 FAIL。
KNOWN_NO_QFQ = {"SH000510", "SH000300", "513650", "159659",
                "513260", "159316", "560390", "513290"}

fails = []


def fail(msg):
    fails.append(msg)


def load_hist(pid):
    p = os.path.join(HIST, pid + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    prods = json.load(open(PROD, encoding="utf-8"))["products"]

    fund_ids = [p["id"] for p in prods if p.get("src") == "fund"]
    em_ids = [p["id"] for p in prods if p.get("src") == "em"]
    cnyield_ids = [p["id"] for p in prods if p.get("src") == "cnyield"]

    # ---- I1 / I2: 基金分红还原（真值驱动） ----
    print(f"[I1/I2] 基金分红还原检查 ({len(fund_ids)} 只)")
    for pid in fund_ids:
        o = load_hist(pid)
        if not o:
            fail(f"{pid} 缺少 history 文件")
            continue
        d, v, u = o.get("d"), o.get("v"), o.get("u")
        if not (d and v and u) or len(v) != len(u):
            fail(f"{pid} 序列缺失/长度不齐 d={len(d) if d else 0} "
                 f"v={len(v) if v else 0} u={len(u) if u else 0}")
            continue
        if "divCum" not in o:
            fail(f"{pid} history 缺 divCum 字段 (未用修复后脚本重跑)")
            continue
        div = o["divCum"]
        has_div = div > REINV_EPS
        reinv = v[-1] - u[-1]

        if has_div:
            # 复权末点必须明显高于单位净值（证明红利再投，非退化 v==u）
            if reinv <= REINV_EPS:
                fail(f"{pid} 有分红(divCum={div:.4f}) 但 v[-1]={v[-1]:.4f} "
                     f"≈ u[-1]={u[-1]:.4f} (v 退化为 u, 未做红利再投)")
            # 复权再投收益不得低于累计分红太多（抓"部分还原/等于 u"）
            elif reinv < div - 0.02:
                fail(f"{pid} 有分红(divCum={div:.4f}) 但 v[-1]-u[-1]={reinv:.4f} "
                     f"< divCum-0.02 (复权未充分还原分红)")
        else:
            # 无分红: v 应等于 u
            if abs(reinv) > 1e-4:
                fail(f"{pid} 无分红(divCum={div:.4f}) 但 v[-1]={v[-1]:.4f} "
                     f"!= u[-1]={u[-1]:.4f}")

    # ---- I3: 股票/ETF/指数前复权 q ----
    print(f"[I3] 股票/ETF/指数前复权 q 检查 ({len(em_ids)} 只)")
    for pid in em_ids:
        o = load_hist(pid)
        if not o:
            fail(f"{pid} 缺少 history 文件")
            continue
        if pid in KNOWN_NO_QFQ:
            # 已知限制(见 KNOWN_NO_QFQ 注释): 腾讯不覆盖该品种 qfq, 缺 q 属合规,
            # 前端回退等比前复权。仅提示, 不判 FAIL。
            if "q" not in o:
                print(f"  NOTE {pid} 缺 q (KNOWN: 腾讯不覆盖该品种 qfq, 前端回退等比前复权)")
            continue
        if "q" not in o:
            fail(f"{pid} 缺 q 序列 (em 源应有前复权)")
            continue
        if len(o["q"]) != len(o["d"]):
            fail(f"{pid} q 长度 {len(o['q'])} != d 长度 {len(o['d'])}")

    # ---- I4: 债券多序列 ----
    print(f"[I4] 债券多序列检查 ({len(cnyield_ids)} 只)")
    for pid in cnyield_ids:
        o = load_hist(pid)
        if not o:
            continue
        for k in ("y1", "y3", "y10"):
            if k not in o:
                fail(f"{pid} 缺债券序列 {k}")

    # ---- GOLDEN: 已知分红基金必须再投 ----
    print("[GOLDEN] 已知分红基金复权再投断言")
    for pid in GOLDEN_FUNDS:
        o = load_hist(pid)
        if not o:
            fail(f"GOLDEN {pid} 缺少 history 文件")
            continue
        if "divCum" not in o:
            fail(f"GOLDEN {pid} 缺 divCum 字段")
            continue
        if "v" not in o or "u" not in o:
            fail(f"GOLDEN {pid} 缺 v/u 序列")
            continue
        reinv = o["v"][-1] - o["u"][-1]
        if o["divCum"] <= 0 or reinv <= GOLDEN_EPS:
            fail(f"GOLDEN {pid} 应有红利再投, divCum={o['divCum']:.4f} "
                 f"v[-1]-u[-1]={reinv:.4f}")

    # ---- 汇总 ----
    print()
    if fails:
        print(f"❌ 自测失败: {len(fails)} 项")
        for f in fails[:60]:
            print("   -", f)
        if len(fails) > 60:
            print(f"   ... 另有 {len(fails)-60} 项")
        sys.exit(1)
    print("✅ 全部自测通过")


if __name__ == "__main__":
    main()
