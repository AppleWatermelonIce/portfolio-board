# -*- coding: utf-8 -*-
"""
指数估值抓取 —— 供看板画 PE/PB 分位色带

主通道：东财妙想 mx-data（读 D:\\CCWorkspace\\.claude\\skills\\mx-data\\mx_data.py，只读调用）
  优点：覆盖中证A500；4 个指数同源，口径统一；历史长（A股 10 年 / A500 全历史）
  需要：环境变量 MX_APIKEY（本机已配置）
备选：蛋卷 danjuanfunds（无需 key，但不收录 A500）
  python fetch_valuation.py --dj

产出 valuation/<id>.json：
  {"id","name","code","src","updated","n","d":[...],"pe":[...],"pb":[...],"cur":{"pe","pb"}}

用法：
  python fetch_valuation.py                 # 妙想抓全部 4 个
  python fetch_valuation.py SH000300        # 只抓指定
  python fetch_valuation.py --dj            # 改用蛋卷（无 A500）
"""
import os, sys, json, glob, shutil, subprocess, datetime, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（scripts/ 的上一级）
VDIR = os.path.join(HERE, "data", "valuation")
TMP = os.path.join(HERE, "data", "_mxout")

MX_SKILL = r'D:\CCWorkspace\.claude\skills\mx-data\mx_data.py'
MX_PY = r'C:\Users\Maple\.workbuddy\binaries\python\envs\default\Scripts\python.exe'

# 看板标的 id -> (妙想问句, 展示名)
MX_TARGETS = {
    "SH000510": ("中证A500 2024年9月至今 每个交易日 市盈率PE(TTM) 市净率PB", "中证A500"),
    "SH000300": ("沪深300指数 近十年 每个交易日 市盈率PE(TTM) 市净率PB",   "沪深300"),
    "SPX":      ("标普500指数 近十年 每个交易日 市盈率PE(TTM) 市净率PB",   "标普500"),
    "NDX100":   ("纳斯达克100指数 近十年 每个交易日 市盈率PE(TTM) 市净率PB", "纳指100"),
}

# 蛋卷备选映射
DJ_MAPPING = {
    "SH000300": ("SH000300", "沪深300"),
    "SPX":      ("SP500",    "标普500"),
    "NDX100":   ("NDX",      "纳指100"),
}


def num(s):
    if s is None: return None
    t = str(s).strip().replace("倍", "").replace(",", "").replace("%", "")
    if t in ("", "-", "--", "null", "None"): return None
    try: return float(t)
    except ValueError: return None


# ---------------- 妙想通道 ----------------
def mx_fetch_one(pid, query, name):
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    r = subprocess.run([MX_PY, MX_SKILL, query, TMP],
                       capture_output=True, timeout=320, cwd=HERE)
    out = r.stdout.decode("utf-8", errors="replace")
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"mx rc={r.returncode} {err[:300]}")
    files = glob.glob(os.path.join(TMP, "*_raw.json"))
    if not files:
        raise RuntimeError("未找到 raw.json；输出：" + out[:300])
    j = json.load(open(files[0], encoding="utf-8"))
    tabs = (j.get("data", {}).get("data", {}).get("searchDataResultDTO", {})
             .get("dataTableDTOList") or [])
    if not tabs:
        raise RuntimeError("返回无表；输出：" + out[:300])
    t = tabs[0]
    raw = t.get("rawTable") or {}
    dates = raw.get("headName") or []
    name_map = t.get("nameMap") or {}
    pe_id = pb_id = None
    for k, v in name_map.items():
        if k == "headNameSub": continue
        if "市盈率" in str(v): pe_id = k
        elif "市净率" in str(v): pb_id = k
    if pe_id is None and pb_id is None:
        raise RuntimeError(f"未识别 PE/PB 列，nameMap={json.dumps(name_map, ensure_ascii=False)[:200]}")
    pe = [num(x) for x in (raw.get(pe_id) or [])] if pe_id else [None] * len(dates)
    pb = [num(x) for x in (raw.get(pb_id) or [])] if pb_id else [None] * len(dates)
    # 对齐长度
    n = min(len(dates), len(pe), len(pb))
    dates, pe, pb = dates[:n], pe[:n], pb[:n]
    # 表头是倒序（新→旧），翻正
    if n > 1 and dates[0] > dates[-1]:
        dates, pe, pb = dates[::-1], pe[::-1], pb[::-1]
    return {
        "id": pid, "name": name, "code": t.get("code", ""), "src": "miaoxiang",
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n": n, "d": dates, "pe": pe, "pb": pb,
        "cur": {"pe": pe[-1] if n else None, "pb": pb[-1] if n else None},
    }


# ---------------- 蛋卷通道（备选） ----------------
def dj_fetch_one(pid, djcode, name):
    import ssl, urllib.request
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    UA = {"User-Agent": "Mozilla/5.0 Chrome/154.0", "Accept": "application/json",
          "Referer": "https://danjuanfunds.com/"}
    def g(u):
        r = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(r, timeout=30, context=ctx) as x:
            return json.loads(x.read().decode("utf-8"))
    def hist(kind):
        j = g(f"https://danjuanfunds.com/djapi/index_eva/{kind}_history/{djcode}?day=all")
        d = j.get("data") or {}
        arr = d.get(f"index_eva_{kind}_growths") or []
        return {datetime.datetime.utcfromtimestamp(p["ts"] / 1000 + 8 * 3600).strftime("%Y-%m-%d"): p[kind]
                for p in arr if p.get(kind) is not None}
    pe, pb = hist("pe"), hist("pb")
    dates = sorted(set(pe) | set(pb))
    return {"id": pid, "name": name, "code": djcode, "src": "danjuan",
            "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "n": len(dates), "d": dates,
            "pe": [pe.get(x) for x in dates], "pb": [pb.get(x) for x in dates],
            "cur": {"pe": pe.get(dates[-1]) if dates else None,
                    "pb": pb.get(dates[-1]) if dates else None}}


def main():
    os.makedirs(VDIR, exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_dj = "--dj" in sys.argv
    want = args or (list(DJ_MAPPING) if use_dj else list(MX_TARGETS))
    ok = miss = 0
    for pid in want:
        if use_dj:
            if pid not in DJ_MAPPING:
                print(f"  [SKIP] {pid} 蛋卷无映射"); continue
            code, name = DJ_MAPPING[pid]
            fn = lambda: dj_fetch_one(pid, code, name)
        else:
            if pid not in MX_TARGETS:
                print(f"  [SKIP] {pid} 无妙想映射"); continue
            q, name = MX_TARGETS[pid]
            fn = lambda: mx_fetch_one(pid, q, name)
        try:
            o = fn()
            p = os.path.join(VDIR, pid + ".json")
            json.dump(o, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  [OK ] {pid} {name:8s} n={o['n']:5d}  {o['d'][0]}..{o['d'][-1]}  "
                  f"PE={o['cur']['pe']} PB={o['cur']['pb']}")
            ok += 1
        except Exception as e:
            print(f"  [ERR] {pid}: {type(e).__name__}: {str(e)[:160]}")
            miss += 1
        time.sleep(1.0)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n完成 {ok} 成功 / {miss} 失败  ->  {VDIR}")


if __name__ == "__main__":
    main()
