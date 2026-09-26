# -*- coding: utf-8 -*-
"""抽查：拆分+分红并存的基金，复权口径是否与官方披露吻合"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from update_data import fetch_fund_nav

CASES = [
    ("481001", "工银核心价值混合A", "含2007年拆分3.63倍 + 多次分红", 16.0, 19.0),
    ("260101", "景顺长城优选混合",  "多次分红", 6.0, 9.0),
    ("519195", "万家品质生活",      "2次分红", 5.0, 7.0),
    ("025776", "鹏华启航量化选股",  "无分红，单位净值应约 -0.7%", 0.99, 1.00),
]

for code, name, feat, lo, hi in CASES:
    try:
        d, v, warn = fetch_fund_nav(code)
        ratio = v[-1] / v[0]
        ok = lo <= ratio <= hi
        print(f"{'[OK]  ' if ok else '[CHECK]'} {code} {name:12s} {feat}")
        print(f"        区间 {d[0]} ~ {d[-1]}   复权倍数 {ratio:.4f}x  "
              f"(即 {(ratio-1)*100:+.2f}%)   区间 = [{lo}x, {hi}x]")
        if warn:
            print(f"        {warn}")
    except Exception as e:
        print(f"[FAIL] {code} {name}: {type(e).__name__} {e}")
    print("-" * 80)
