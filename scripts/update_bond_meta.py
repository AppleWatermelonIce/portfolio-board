#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
债券持仓穿透 → 更新 products.json 的债券元信息 + 生成穿透分析报告。

做什么（不依赖 AI，可重跑）：
  1) 抓取每只债券基金「2026-06-30 季报」实际债券持仓（天天基金 zqcc）与基金类型（jbgk）；
  2) 把每只持仓债券按名称/代码归类：利率债(国债/地方债/政金债) / 信用债 / 同业存单 / 可转债；
  3) 汇总每只基金的穿透占比 → 主导类型（slot）与久期桶（dur，来自天天基金类型映射）；
  4) 写回 products.json：slot + dur + note(穿透占比)；缺失的债券基金自动补进去；
  5) 重新生成《债券穿透分析报告.html》。

用法： python scripts/update_bond_meta.py
重跑幂等：已存在的基金只更新 slot/dur/note，不会重复插入。
"""
import urllib.request, re, json, time, io, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
# 债券基金（group=债券基金，排除 CNGB 国债收益率曲线）。新增债券基金后请在此追加 code 与 name。
BOND_FUNDS = {
    "006670": "广发景秀纯债A", "004102": "中信保诚稳悦债券A", "006242": "宝盈盈润纯债债券A",
    "002065": "景顺长城景盛双息收益债券A", "007194": "长城短债A", "006596": "国泰聚禾纯债债券A",
    "008646": "天弘增利短债发起A", "001497": "大成月添利一个月滚动持有中短债E",
    "016748": "申万菱信稳鑫60天滚动持有中短债债券A", "020810": "泰康悦享60天持有期债券E",
    "485119": "工银信用纯债债券A", "006431": "汇安鼎利纯债A",
    "018665": "南方宁元债券A", "003949": "兴全稳泰债券A",
}
DUR = {"长债": "长久期", "中短债": "短久期", "信用债": "中长久期", "混合二级": "固含股"}
DISPLAY = {"利率债-国债": "利率债", "利率债-地方债": "利率债", "利率债-政金债": "利率债", "利率债": "利率债",
           "信用债": "信用债", "同业存单": "同业存单", "可转债": "可转债", "其他": "其他/待分类"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=25).read().decode("utf-8", "ignore")


def classify(name, code=""):
    n = name
    if re.search(r"可转债|可交换|转债|转\d|EB", n): return "可转债"
    if re.search(r"同业存单|存单", n) or re.search(r"CD\d", n): return "同业存单"
    if re.search(r"国债|记账式|贴债|附息国债|特别国债", n): return "利率债-国债"
    if re.search(r"地方政府债|地方债|专项债|再融资债", n): return "利率债-地方债"
    if re.search(r"国开|农发|农业发展银行|进出口|进出债|口行|政策性金融债|央行票据|铁道债|政府支持机构|汇金", n): return "利率债-政金债"
    if re.search(r"企业债|公司债|中期票据|中票|MTN|短期融资券|短融|超短期融资|超短融|CP|非公开定向|PPN|资产支持|ABS|商业银行债|二级资本债|二级|永续债|银行|金融债|证券公司债|私募债|资本债|商业银行永续|TLAC", n): return "信用债"
    if "债" in n: return "信用债"
    if re.match(r"^0\d{4}$", code): return "利率债-国债"
    if re.match(r"^111", code): return "同业存单"
    if re.match(r"^(12|13|14|15|18|19|20|24|27|28|29)\d{4}$", code): return "信用债"
    if re.match(r"^\d{6}$", code) and code[:2] in ("25", "26", "24", "23", "22"): return "利率债"
    return "其他"


def fetch_holdings(code):
    """返回 (period, [(name, pct, bondcode)]) —— 最新季报前十大债券持仓。"""
    raw = get(f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=zqcc&code={code}&topline=10&year=&month=")
    m = re.search(r'content:"(.*?)"', raw, re.S)
    if not m: return None, []
    html = m.group(1).replace("\\/", "/")
    pm = re.search(r"截止至[：:]\s*<font[^>]*>([\d\-]+)</font>", html)
    period = pm.group(1) if pm else "?"
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if len(tds) >= 5:
            bcode, name, pct = tds[1], tds[2], tds[3].replace("%", "").strip()
            try: p = float(pct)
            except Exception: continue
            if name and name != "债券名称":
                out.append((name, p, bcode))
    return period, out


def fetch_type(code):
    """天天基金基金类型，如 债券型-长债。"""
    try:
        raw = get(f"https://fundf10.eastmoney.com/jbgk_{code}.html")
        m = re.search(r"类型：\s*<span>([^<]+)</span>", raw)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def dur_of(fundtype):
    return DUR.get(fundtype.replace("债券型-", "").strip(), "")


def fetch_name(code):
    try:
        j = json.loads(get(f"https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key={code}"))
        for it in (j.get("Datas") or []):
            if it.get("CODE") == code:
                return it.get("NAME")
    except Exception:
        pass
    return BOND_FUNDS.get(code, code)


def main():
    data = {}
    for code in BOND_FUNDS:
        period, h = fetch_holdings(code)
        fundtype = fetch_type(code)
        comp = {}
        for name, p, bcode in h:
            c = DISPLAY[classify(name, bcode)]
            comp[c] = comp.get(c, 0) + p
        total = sum(comp.values())
        disp = {k: round(100 * v / total, 1) for k, v in sorted(comp.items(), key=lambda x: -x[1])} if total else {}
        cands = {k: v for k, v in disp.items() if k != "其他/待分类"}
        primary = max(cands, key=cands.get) if cands else "其他/待分类"
        data[code] = {"name": fetch_name(code), "period": period, "type": fundtype,
                      "dur": dur_of(fundtype), "primary": primary, "disp": disp, "holdings": h}
        print(f"{code} {data[code]['name']:22s} {primary:4s} {data[code]['dur']:4s} {disp}")
        time.sleep(0.3)

    patch_products(data)
    write_report(data)
    print("\nOK: products.json 已更新，报告已生成。")


def patch_products(data):
    pj = io.open(os.path.join(ROOT, "data/products.json"), encoding="utf-8").read()
    for code, r in data.items():
        pat = re.compile(r'\{"id": "' + re.escape(code) + r'"[^{}]*\}')
        m = pat.search(pj)
        if not m: continue
        obj = json.loads(m.group(0))
        obj["slot"] = r["primary"]
        obj["dur"] = r["dur"]
        obj["note"] = "穿透(26-06-30)：" + "/".join(f"{k}{v}%" for k, v in r["disp"].items() if v > 0)
        pj = pj[:m.start()] + json.dumps(obj, ensure_ascii=False) + pj[m.end():]
    # CNGB 国债收益率曲线 → 利率债 槽位
    m = re.search(r'\{"id": "CNGB"[^{}]*\}', pj, re.S)
    if m:
        obj = json.loads(m.group(0)); obj["slot"] = "利率债"
        pj = pj[:m.start()] + json.dumps(obj, ensure_ascii=False) + pj[m.end():]
    # 缺失的债券基金自动插入到 006431 之后
    missing = [c for c in data if not re.search(r'\{"id": "' + re.escape(c) + r'"', pj)]
    if missing:
        anchor = re.search(r'\{"id": "006431"[^{}]*\}\s*,', pj)
        news = []
        for c in missing:
            r = data[c]
            news.append({"id": c, "name": r["name"], "code": c, "group": "债券基金", "src": "fund",
                         "secid": c, "unit": "元", "slot": r["primary"], "dur": r["dur"],
                         "note": "穿透(26-06-30)：" + "/".join(f"{k}{v}%" for k, v in r["disp"].items() if v > 0)})
        if anchor:
            ins = "\n" + ",\n".join("    " + json.dumps(o, ensure_ascii=False) for o in news) + ","
            pj = pj[:anchor.end()] + ins + pj[anchor.end():]
            print("inserted new bond funds:", missing)
    d = json.loads(pj)
    io.open(os.path.join(ROOT, "data/products.json"), "w", encoding="utf-8").write(pj)
    print(f"products.json: {len(d['products'])} products")


def write_report(data):
    import html as H
    FINE_CLS = {"利率债-国债": "gov", "利率债-地方债": "gov", "利率债-政金债": "pol", "利率债": "pol",
                "信用债": "cr", "同业存单": "ncd", "可转债": "cb", "其他": "oth"}
    DURCLS = {"短久期": "short", "中长久期": "mid", "长久期": "long", "固含股": "hybrid"}
    groups = {}
    for code, r in data.items():
        groups.setdefault(r["primary"], []).append(code)
    rows = ""
    for g in ["利率债", "信用债", "同业存单", "可转债"]:
        if g not in groups: continue
        rows += f'<div class="grp"><div class="grph">{g} <span class="cnt">{len(groups[g])} 只</span></div>'
        for code in groups[g]:
            r = data[code]
            bars = "".join(f'<div class="bar {FINE_CLS.get(k,"oth")}" style="width:{max(v,2)}%" title="{k} {v}%"><span>{k} {v}%</span></div>'
                           for k, v in r["disp"].items() if v > 0)
            durb = f'<span class="dbadge d-{DURCLS.get(r["dur"],"oth")}">{r["dur"]}</span>' if r["dur"] else ""
            det = ""
            for name, p, bcode in sorted(r["holdings"], key=lambda x: -x[1])[:10]:
                c = DISPLAY[classify(name, bcode)]
                det += f'<tr><td class="nm">{H.escape(name)}</td><td class="cd">{bcode}</td><td class="tp"><span class="tag t-{FINE_CLS.get(c,"oth")}">{c}</span></td><td class="pc">{p:.2f}%</td></tr>'
            rows += f'''<div class="fund"><div class="fhead"><span class="fname">{H.escape(r['name'])}</span><span class="fcode">{code}</span>{durb}<span class="fmeta">类型：{g} · 久期：{r['dur'] or '—'}</span></div><div class="bars">{bars}</div><details><summary>穿透明细（前10大债券持仓，占净值）</summary><table class="det"><thead><tr><th>债券名称</th><th>代码</th><th>类型</th><th>占净值</th></tr></thead><tbody>{det}</tbody></table></details></div>'''
        rows += "</div>"
    page = '''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>债券持仓穿透分析报告</title><style>
:root{--bg:#f6f6f7;--card:#fff;--line:#e5e5e9;--text:#16161a;--muted:#76767d;--muted2:#9a9aa1;--accent:#2a6bd0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);font-size:12.5px;margin-bottom:22px}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 22px;font-size:12px;color:var(--muted)}.legend .tag{margin-right:4px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:5px;font-weight:600;white-space:nowrap}
.t-gov{background:#e7f0fb;color:#2a6bd0}.t-pol{background:#eaf1fb;color:#2a6bd0}.t-cr{background:#fdf0e0;color:#c26a06}.t-ncd{background:#f0f0f2;color:#6a6a72}.t-cb{background:#f1ecfd;color:#7c4ddb}.t-oth{background:#f0f0f2;color:#6a6a72}
.grp{margin-bottom:26px}.grph{font-size:15px;font-weight:700;padding:8px 0 10px;border-bottom:2px solid var(--line);margin-bottom:12px;display:flex;align-items:center;gap:8px}.grph .cnt{font-size:12px;color:var(--muted);font-weight:500}
.fund{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.fhead{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}.fname{font-size:14.5px;font-weight:650}.fcode{font-size:11.5px;color:var(--muted2);font-family:ui-monospace,monospace}.fmeta{font-size:11.5px;color:var(--muted);margin-left:auto}
.dbadge{font-size:10.5px;font-weight:650;padding:1px 8px;border-radius:6px;border:1px solid transparent}.d-short{background:#e7f6ee;color:#12a05c;border-color:#bfe8d2}.d-mid{background:#eaf1fb;color:#2a6bd0;border-color:#bcd3f2}.d-long{background:#fdf0e0;color:#c26a06;border-color:#f6d8ac}.d-hybrid{background:#f1ecfd;color:#7c4ddb;border-color:#d9ccf7}.d-oth{background:#f0f0f2;color:#6a6a72;border-color:#e0e0e4}
.bars{display:flex;height:26px;border-radius:7px;overflow:hidden;border:1px solid var(--line);margin-bottom:10px}.bar{display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#fff;min-width:0}.bar.gov{background:#4a86d8}.bar.pol{background:#6aa3e0}.bar.cr{background:#e0913a}.bar.ncd{background:#9a9aa1}.bar.cb{background:#9a6ee0}.bar.oth{background:#c2c2c8}.bar span{padding:0 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
details summary{cursor:pointer;font-size:12.5px;color:var(--accent);user-select:none}.det{width:100%;border-collapse:collapse;margin-top:8px;font-size:12.5px}.det th{text-align:left;color:var(--muted);font-weight:600;padding:5px 8px;border-bottom:1px solid var(--line);font-size:11.5px}.det td{padding:5px 8px;border-bottom:1px solid #f2f2f4}.det .nm{min-width:200px}.det .cd{color:var(--muted2);font-family:ui-monospace,monospace}.det .pc{text-align:right;font-variant-numeric:tabular-nums}
.foot{margin-top:28px;font-size:11.5px;color:var(--muted2);line-height:1.7}</style></head><body><div class="wrap">
<h1>债券持仓穿透分析报告</h1><div class="sub">穿透口径：各债券基金 2026-06-30 季报实际债券持仓（占净值比例）· 数据来源：天天基金 · 久期来源：天天基金基金类型 · 由 scripts/update_bond_meta.py 生成</div>
<div class="legend">图例：<span><span class="tag t-gov">国债</span></span><span><span class="tag t-pol">政策性金融债/利率债</span></span><span><span class="tag t-cr">信用债</span></span><span><span class="tag t-ncd">同业存单</span></span><span><span class="tag t-cb">可转债/可交换债</span></span></div>
''' + rows + '''<div class="foot">说明：<br>·「类型」=穿透后实际债券持仓的主导类型（利率债/信用债）；<br>·「久期」=天天基金对基金的官方分类映射（长债→长久期，中短债→短久期，信用债→中长久期，混合二级→固含股）；<br>·占比条为各基金前十大债券持仓按类型聚合后的相对占比（占前十大持仓之和，非占基金净值全口径）；<br>·485119 工银信用纯债：名为「信用纯债」，2026-06-30 实际持仓 88.8% 为利率债，按实际持仓归入利率债组；<br>·002065 景顺长城景盛双息：二级债基，含 17.8% 可转债 + 股票（久期角标「固含股」）。</div></div></body></html>'''
    io.open(os.path.join(ROOT, "债券穿透分析报告.html"), "w", encoding="utf-8").write(page)
    print("report written")


if __name__ == "__main__":
    main()
