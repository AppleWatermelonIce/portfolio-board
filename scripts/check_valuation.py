# -*- coding: utf-8 -*-
"""
估值数据自检：
1) 用全历史自算分位，与蛋卷官方 pe_percentile / pb_percentile 交叉验证
2) 输出 5 档（20/40/60/80）分位阈值，供色带使用
"""
import os, json, glob, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（scripts/ 的上一级）
VDIR = os.path.join(HERE, "data", "valuation")

def pct_rank(hist, v):
    """v 在 hist 中的百分位（<= 占比）"""
    if v is None: return None
    n = len(hist)
    c = sum(1 for x in hist if x <= v)
    return c / n

def quantile(hist, q):
    """线性插值分位数"""
    s = sorted(hist)
    if not s: return None
    pos = q * (len(s) - 1)
    lo = int(pos); hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac

def main():
    files = sorted(glob.glob(os.path.join(VDIR, "*.json")))
    if not files:
        print("无数据，请先跑 fetch_valuation.py"); return
    print(f"{'标的':10s} {'指标':4s} {'当前值':>9s} {'自算分位':>9s} {'官方分位':>9s} {'差':>7s}  {'5档阈值 20/40/60/80'}")
    print("-" * 108)
    bad = 0
    for f in files:
        o = json.load(open(f, encoding="utf-8"))
        for kind in ("pe", "pb"):
            hist = [v for v in o[kind] if v is not None]
            if len(hist) < 30: 
                print(f"{o['name']:10s} {kind.upper():4s} 样本不足({len(hist)})，跳过")
                continue
            cur = o["cur"].get(kind)
            off = o["cur"].get(kind + "_pct")
            mine = pct_rank(hist, cur)
            diff = abs((mine or 0) - (off or 0))
            th = [quantile(hist, q) for q in (0.2, 0.4, 0.6, 0.8)]
            flag = "  " if diff < 0.03 else "⚠ "
            if diff >= 0.03: bad += 1
            ths = " / ".join(f"{x:.2f}" for x in th)
            print(f"{flag}{o['name']:10s} {kind.upper():4s} {cur:9.4f} {mine*100:8.2f}% "
                  f"{(off or 0)*100:8.2f}% {diff*100:6.2f}%  {ths}")
    print("-" * 108)
    print(f"交叉验证：{'全部一致（差<3pp）' if bad == 0 else str(bad)+' 项超差，需检查口径'}")

if __name__ == "__main__":
    main()
