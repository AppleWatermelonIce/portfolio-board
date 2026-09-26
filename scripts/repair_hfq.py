# -*- coding: utf-8 -*-
"""
耐心修复：把 secid 属于 东财/腾讯 通道的标的，用【后复权】口径重新全量拉取。

背景：前复权在长周期会得到负价格（累计现金分红超过当期股价），
      导致"成立来"收益率荒谬（美的 -778%、立讯 -33011%）。
      腾讯/东财因本轮调试被限流，故此脚本以很低的频率串行为之，
      失败就等更久再试，最多若干轮。

用法：python repair_hfq.py [轮数]
"""
import json, os, sys, time, ssl, urllib.request, urllib.parse, random
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（scripts/ 的上一级）
HIST = os.path.join(HERE, "data", "history")
DATA = os.path.join(HERE, "data")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
_ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 4


def get(u, ref=None, t=30):
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*", "Connection": "close"})
    if ref: req.add_header("Referer", ref)
    with urllib.request.urlopen(req, timeout=t, context=_ctx) as r:
        return r.read().decode("utf-8", "replace")


def em2tx(secid):
    if "." not in secid: return None
    m, c = secid.split(".", 1)
    return {"0": "sz" + c, "1": "sh" + c, "116": "hk" + c.zfill(5)}.get(m)


def fetch_em(secid):
    q = urllib.parse.urlencode({"secid": secid, "klt": "101", "fqt": "2",
                                "beg": "19900101", "end": "20500101",
                                "fields1": "f1", "fields2": "f51,f53"})
    j = json.loads(get("https://push2his.eastmoney.com/api/qt/stock/kline/get?" + q,
                       "https://quote.eastmoney.com/"))
    k = j["data"]["klines"]
    d = [x.split(",")[0] for x in k]
    v = [round(float(x.split(",")[1]), 6) for x in k]
    return d, v


def _tx_raw(code, end):
    """⚠ 用 newfqkline 端点：老的 fqkline 被限流返回 501，newfqkline 属独立限流桶，通常仍可用"""
    p = f"{code},day,,{end},640,hfq"
    for host in ("https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
                 "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"):
        try:
            j = json.loads(get(host + "?param=" + urllib.parse.quote(p)))
            d0 = j.get("data")
            if isinstance(d0, dict):
                kk = d0.get(code) or {}
                # 无除权记录的标的（指数、部分ETF）不产出 hfqday，此时 'day' 即等价
                arr = kk.get("hfqday") or kk.get("day") or []
                if arr:
                    return arr
        except Exception:
            continue
    return []


def fetch_tx(secid, max_seg=20):
    code = em2tx(secid)
    if not code: raise RuntimeError("不支持")
    seen, end = {}, ""
    for _ in range(max_seg):
        arr = _tx_raw(code, end)
        if not arr: break
        for r in arr: seen[r[0]] = (r[0], round(float(r[2]), 6))
        old = arr[0][0]
        if len(arr) < 640: break
        end = (datetime.strptime(old, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        time.sleep(0.4)
    if not seen: raise RuntimeError("腾讯无数据")
    ds = sorted(seen)
    return ds, [seen[x][1] for x in ds]


def main():
    with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
        prods = json.load(f)["products"]
    targets = [p for p in prods if p.get("src") in ("em",) and p.get("chan", "auto") in ("auto",)]
    print(f"待修复 {len(targets)} 个标的，最多 {ROUNDS} 轮", flush=True)

    for rnd in range(1, ROUNDS + 1):
        todo = []
        for p in targets:
            fp = os.path.join(HIST, p["id"] + ".json")
            if os.path.exists(fp):
                try:
                    o = json.load(open(fp, encoding="utf-8"))
                    if o.get("chan") in ("东财", "腾讯") and o.get("fq") == "hfq":
                        continue
                except Exception:
                    pass
            todo.append(p)
        if not todo:
            print("全部已修复完毕。", flush=True)
            return 0
        print(f"\n===== 第 {rnd}/{ROUNDS} 轮，剩余 {len(todo)} 个 =====", flush=True)
        for p in todo:
            got, ch = None, ""
            for nm, fn in (("东财", fetch_em), ("腾讯", fetch_tx)):
                try:
                    got = fn(p["secid"])
                    if got and len(got[0]) > 1 and min(got[1]) > 0:
                        ch = nm
                        break
                    got = None
                except Exception as e:
                    print(f"    {nm}失败 {type(e).__name__}: {str(e)[:60]}", flush=True)
                time.sleep(1.5)
            if not got:
                print(f"  [未成功] {p['name']}", flush=True)
                time.sleep(random.uniform(4, 7))
                continue
            d, v = got
            obj = {"id": p["id"], "name": p["name"], "code": p["code"], "group": p["group"],
                   "src": p["src"], "secid": p["secid"], "unit": p.get("unit", ""),
                   "note": p.get("note", ""), "chan": ch, "fq": "hfq",
                   "fulled": datetime.now().strftime("%Y-%m-%d"),
                   "last": d[-1], "d": d, "v": v}
            tmp = os.path.join(HIST, p["id"] + ".json.tmp")
            json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, os.path.join(HIST, p["id"] + ".json"))
            print(f"  [OK/{ch}] {p['name']:16s} n={len(d):5d}  {d[0]} ~ {d[-1]}  最小值={min(v):.3f}", flush=True)
            time.sleep(random.uniform(2.5, 4.5))
        time.sleep(45)
    return 0


if __name__ == "__main__":
    sys.exit(main())
