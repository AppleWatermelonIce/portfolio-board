# -*- coding: utf-8 -*-
"""
把 valuation/*.json 打包成 valuation.js（window.VALUATION_DATA = {...}）
供 index.html 以 <script src> 加载（file:// 下 fetch 被同源策略禁止，必须走 script 标签）

同时预计算 5 档分位阈值（主人给定档位）：
  低估   0–15%   深绿
  较低  15–35%   浅绿
  适中  35–70%   黄
  较高  70–85%   浅红
  高估  85–100%  深红
"""
import os, json, glob, datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（scripts/ 的上一级）
VDIR = os.path.join(HERE, "data", "valuation")
ASSETS = os.path.join(HERE, "assets")

# 主人指定的 5 档（分位上限, 名称, 颜色）
BANDS = [
    (0.15, "低估",   "#1a7a3c"),   # 深绿
    (0.35, "较低",   "#7fc98f"),   # 浅绿
    (0.70, "适中",   "#e0b400"),   # 黄
    (0.85, "较高",   "#e8837b"),   # 浅红
    (1.00, "高估",   "#b3231b"),   # 深红
]


def quantile(sorted_vals, q):
    if not sorted_vals: return None
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos); hi = min(lo + 1, len(sorted_vals) - 1)
    f = pos - lo
    return sorted_vals[lo] * (1 - f) + sorted_vals[hi] * f


def pct_rank(hist, v):
    if v is None: return None
    return sum(1 for x in hist if x <= v) / len(hist)


def band_of(p):
    for hi, name, color in BANDS:
        if p <= hi: return name, color
    return BANDS[-1][1], BANDS[-1][2]


def main():
    files = sorted(glob.glob(os.path.join(VDIR, "*.json")))
    out = {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "bands": [{"hi": h, "name": n, "color": c} for h, n, c in BANDS],
           "src": "", "items": {}}
    print(f"{'标的':10s} {'源':9s} {'点数':>6s} {'区间':24s} {'当前PE':>8s} {'PE分位':>8s} {'当前PB':>8s} {'PB分位':>8s}")
    print("-" * 92)
    for f in files:
        o = json.load(open(f, encoding="utf-8"))
        pid = o["id"]
        item = {"name": o["name"], "code": o.get("code", ""), "src": o.get("src", ""),
                "d": o["d"], "pe": o["pe"], "pb": o["pb"], "cur": {}}
        for kind in ("pe", "pb"):
            hist = sorted(v for v in o[kind] if v is not None)
            if len(hist) < 30:
                item["cur"][kind] = {"v": None, "pct": None, "band": None,
                                     "th": None, "n": len(hist)}
                continue
            cur = o["cur"].get(kind)
            p = pct_rank(hist, cur)
            name, color = band_of(p)
            item["cur"][kind] = {
                "v": cur, "pct": round(p, 4), "band": name, "color": color,
                # 5 档阈值（按分位 15/35/70/85 对应数值），供画水平色带
                "th": [round(quantile(hist, q), 4) for q in (0.15, 0.35, 0.70, 0.85)],
                "n": len(hist),
            }
        out["items"][pid] = item
        out["src"] = o.get("src", "")
        c = item["cur"]
        fp = lambda k: (f"{c[k]['v']:.2f}" if c[k]["v"] is not None else "—")
        pp = lambda k: (f"{c[k]['pct']*100:.1f}%" if c[k]["pct"] is not None else "—")
        print(f"{o['name']:10s} {o.get('src',''):9s} {o['n']:6d} "
              f"{o['d'][0]}..{o['d'][-1]}  {fp('pe'):>8s} {pp('pe'):>8s} {fp('pb'):>8s} {pp('pb'):>8s}")
    p = os.path.join(ASSETS, "valuation.js")
    with open(p, "w", encoding="utf-8") as f:
        f.write("window.VALUATION_DATA = ")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    sz = os.path.getsize(p) / 1024
    print("-" * 92)
    print(f"已写入 {p}  ({sz:.0f} KB)  标的数 {len(out['items'])}")


if __name__ == "__main__":
    main()
