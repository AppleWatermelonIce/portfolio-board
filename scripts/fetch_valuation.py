# -*- coding: utf-8 -*-
"""
指数估值抓取 —— 供看板画 PE/PB 分位色带

【通道策略：三个通道，默认全免 key】
  ★ 蛋卷 danjuanfunds（免 key）：沪深300 / 标普500 / 纳指100，约 515 点（周频，10 年）
  ★ 中证官网 + 自算 ROE（免 key）：中证A500，见 scripts/calc_a500.py
      - PE 用中证 index-perf 接口的 peg 字段（= 官方 PE-TTM，已逐日比对蛋卷验证）
      - PB 用恒等式 PB = PE × ROE，ROE = Σ净利润(TTM)/Σ净资产 由东财 F10 财务自算
      - 覆盖 2024-09 指数发布至今（官网更早年份该字段为空）
  ★ 东财妙想 mx-data（需 MX_APIKEY）：**已非必需**，仅在 --mx 时用作对照

这样设计的目的：**部署到服务器后整条链路不依赖任何 key**。

用法：
  python fetch_valuation.py            # 默认：蛋卷 3 个 + A500 自算（全免 key）
  python fetch_valuation.py --no-mx    # 同上（保留兼容）
  python fetch_valuation.py --mx       # 强制全部走妙想（需 key，用于口径对照）
  python fetch_valuation.py SH000300   # 只抓指定

产出 data/valuation/<id>.json：
  {"id","name","code","src","updated","n","d":[...],"pe":[...],"pb":[...],"cur":{"pe","pb"}}
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

# 蛋卷通道映射
DJ_MAPPING = {
    "SH000300": ("SH000300", "沪深300"),
    "SPX":      ("SP500",    "标普500"),
    "NDX100":   ("NDX",      "纳指100"),
}

# 免 key 自算通道（中证官网 PE + 自算 ROE）
CALC_TARGETS = {"SH000510": "中证A500"}


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
    if not dates:
        raise RuntimeError("蛋卷返回空序列")
    # 当前值取各自序列最后一个非零点（pe / pb 末日可能不同步）
    def last_of(m):
        for x in reversed(dates):
            if m.get(x): return m[x]
        return None
    return {"id": pid, "name": name, "code": djcode, "src": "danjuan",
            "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "n": len(dates), "d": dates,
            "pe": [pe.get(x) for x in dates], "pb": [pb.get(x) for x in dates],
            "cur": {"pe": last_of(pe), "pb": last_of(pb)}}


# ---------------- 免 key 自算通道（中证A500） ----------------
def calc_fetch_one(pid):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "calc_a500", os.path.join(HERE, "scripts", "calc_a500.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.build(m.INDEX_CODE, m.ETF, pid, CALC_TARGETS[pid])


def existing(pid):
    """已有的本地 JSON（用于 A500 无 key 时沿用）"""
    p = os.path.join(VDIR, pid + ".json")
    if not os.path.exists(p): return None
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return None


def main():
    os.makedirs(VDIR, exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force_mx = "--mx" in sys.argv
    no_mx = "--no-mx" in sys.argv
    has_key = bool(os.environ.get("MX_APIKEY"))
    want = args or list(MX_TARGETS)
    # 排序：蛋卷先跑（A500 的水平校准要读蛋卷沪深300 的当前 PE/PB）
    want = sorted(want, key=lambda p: 0 if p in DJ_MAPPING else 1)
    ok = miss = skip = 0

    print(f"通道：蛋卷（免 key）+ A500 自算（免 key）"
          + ("，妙想可用（MX_APIKEY 已设置）" if has_key else "，妙想不可用（无 MX_APIKEY，不影响）")
          + ("  [--mx 强制妙想]" if force_mx else ""))
    print("-" * 96)

    for pid in want:
        ch = None
        if force_mx:
            ch = "mx" if pid in MX_TARGETS else None
        elif pid in DJ_MAPPING:
            ch = "dj"                                  # 蛋卷覆盖的，一律走蛋卷
        elif pid in CALC_TARGETS:
            ch = "calc"                                # 中证A500：官网 PE + 自算 ROE，免 key

        if ch is None:
            old = existing(pid)
            if old:
                print(f"  [沿用] {pid} {old['name']:8s} 无可用通道，保留 {old.get('src')} 数据 "
                      f"截止 {old['d'][-1]}（n={old['n']}）")
            else:
                print(f"  [SKIP] {pid} 无可用通道，页面将不显示其估值底色")
            skip += 1
            continue

        if ch == "dj":
            code, name = DJ_MAPPING[pid]
            fn = lambda: dj_fetch_one(pid, code, name)
        elif ch == "calc":
            name = CALC_TARGETS[pid]
            fn = lambda: calc_fetch_one(pid)
        else:
            q, name = MX_TARGETS[pid]
            fn = lambda: mx_fetch_one(pid, q, name)
        try:
            o = fn()
            p = os.path.join(VDIR, pid + ".json")
            json.dump(o, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  [OK ] {pid:9s} {name:8s} [{o['src']:9s}] n={o['n']:5d}  "
                  f"{o['d'][0]}..{o['d'][-1]}  PE={o['cur']['pe']} PB={o['cur']['pb']}")
            ok += 1
        except Exception as e:
            print(f"  [ERR] {pid}: {type(e).__name__}: {str(e)[:160]}")
            miss += 1
        time.sleep(1.0)
    shutil.rmtree(TMP, ignore_errors=True)
    print("-" * 96)
    print(f"完成  成功 {ok} / 失败 {miss} / 沿用或跳过 {skip}   ->  {VDIR}")


if __name__ == "__main__":
    main()
