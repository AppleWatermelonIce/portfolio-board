# -*- coding: utf-8 -*-
"""
本地每日估值更新（指数 PE/PB）—— 纯脚本，不依赖 AI、不依赖 GitHub。

【为什么需要它】
线上站点由 GitHub Actions 每天 23:30（北京时间）自动重算并部署，但那只更新
GitHub Pages 那一份；本机 file:// 直开的 index.html 读的是本地 assets/valuation.js，
不会自己变新（assets/live.js 的页面自动刷新只补行情，完全不碰估值）。
本脚本负责把**本机**这份也刷成最新。

【流程】
  1) fetch_valuation.py     抓 PE/PB：蛋卷 3 个（沪深300/标普500/纳指100）+ 中证A500 自算
  2) build_valuation_js.py  打包 -> assets/valuation.js（含 5 档分位阈值）

【产出】
  assets/valuation.js、data/valuation/*.json

【日志与故障提示】
  logs/估值更新_YYYY-MM.log   追加写，每次运行一段
  logs/估值更新失败.md        失败时写入，成功自动删除 —— 这是给主人的"明显故障提示"
  退出码                       0=成功，1=失败（计划任务「上次运行结果」会显示非 0）

【判定标准】
  失败：任一步骤退出码非 0 / 抓取输出含 [ERR] / 4 个指数任一估值文件缺失或损坏
  警告：某指数序列 > WARN_DAYS 天未前进（不判失败，只在日志里标注）

用法：
  python scripts/update_valuation.py
"""
import os
import sys
import json
import subprocess
import datetime

# 中文输出：Windows 控制台默认 cp936，强制 UTF-8 以免乱码
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 项目根
SCRIPTS = os.path.join(HERE, "scripts")
LOGDIR = os.path.join(HERE, "logs")
VDIR = os.path.join(HERE, "data", "valuation")
ASSETS_JS = os.path.join(HERE, "assets", "valuation.js")
FAIL_MARK = os.path.join(LOGDIR, "估值更新失败.md")

# 必须齐备的 4 个指数（与 fetch_valuation.py 的通道一一对应）
EXPECT = [("SH000300", "沪深300"), ("SPX", "标普500"),
          ("NDX100", "纳指100"), ("SH000510", "中证A500")]
WARN_DAYS = 12          # 序列超过这么多天未前进就告警（蛋卷为周频，正常滞后约 3~7 天）


def run_step(script, args=()):
    """跑一个子脚本，返回 (returncode, stdout, stderr)"""
    cmd = [sys.executable, os.path.join(SCRIPTS, script), *args]
    p = subprocess.run(cmd, cwd=HERE, capture_output=True)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def main():
    os.makedirs(LOGDIR, exist_ok=True)
    t0 = datetime.datetime.now()
    logpath = os.path.join(LOGDIR, "估值更新_%s.log" % t0.strftime("%Y-%m"))

    lines = []

    def log(s=""):
        print(s)
        lines.append(s)

    log("=" * 78)
    log("本地估值更新（指数 PE/PB）  %s" % t0.strftime("%Y-%m-%d %H:%M:%S"))
    log("python: %s" % sys.executable)
    log("=" * 78)

    # ---------- 步骤 1：抓取 ----------
    rc1, out1, err1 = run_step("fetch_valuation.py")
    log("")
    log("--- [1/2] fetch_valuation.py   rc=%d ---" % rc1)
    log(out1.rstrip())
    if err1.strip():
        log("[stderr] " + err1.rstrip())

    # ---------- 步骤 2：打包 ----------
    rc2, out2, err2 = run_step("build_valuation_js.py")
    log("")
    log("--- [2/2] build_valuation_js.py   rc=%d ---" % rc2)
    log(out2.rstrip())
    if err2.strip():
        log("[stderr] " + err2.rstrip())

    # ---------- 产出核对（不信任子脚本的退出码，独立复查产物） ----------
    problems, warns = [], []

    if rc1 != 0:
        problems.append("fetch_valuation.py 退出码 %d" % rc1)
    if rc2 != 0:
        problems.append("build_valuation_js.py 退出码 %d" % rc2)
    for line in out1.splitlines():
        if "[ERR]" in line:
            problems.append("抓取失败 " + line.strip())

    today = datetime.date.today()
    log("")
    log("--- 产出核对 ---")
    for pid, name in EXPECT:
        p = os.path.join(VDIR, pid + ".json")
        if not os.path.exists(p):
            problems.append("%s(%s) 估值文件缺失" % (name, pid))
            continue
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception as ex:
            problems.append("%s(%s) JSON 解析失败: %s" % (name, pid, ex))
            continue
        ds = j.get("d") or []
        cur = j.get("cur") or {}
        last = ds[-1] if ds else None
        age = None
        if last:
            try:
                age = (today - datetime.date.fromisoformat(last)).days
            except ValueError:
                problems.append("%s(%s) 末点日期异常: %r" % (name, pid, last))
        log("  %-9s %-8s src=%-12s n=%-4s last=%s  PE=%-9s PB=%-9s %s" % (
            pid, name, j.get("src", "-"), j.get("n", "-"), last,
            cur.get("pe"), cur.get("pb"),
            ("(%d 天前)" % age) if age is not None else ""))
        if not ds:
            problems.append("%s(%s) 序列为空" % (name, pid))
        elif age is not None and age > WARN_DAYS:
            warns.append("%s(%s) 序列已 %d 天未前进" % (name, pid, age))

    if not os.path.exists(ASSETS_JS):
        problems.append("assets/valuation.js 未生成")
    else:
        m = datetime.datetime.fromtimestamp(os.path.getmtime(ASSETS_JS))
        log("  assets/valuation.js  写于 %s  (%.0f KB)" % (
            m.strftime("%Y-%m-%d %H:%M"), os.path.getsize(ASSETS_JS) / 1024.0))
        if (t0 - m).total_seconds() > 600:
            warns.append("valuation.js 时间戳未刷新，可能没被重写")

    # ---------- 结论 ----------
    for w in warns:
        log("[警告] " + w)
    ok = not problems
    log("")
    if ok:
        if os.path.exists(FAIL_MARK):
            os.remove(FAIL_MARK)
            log("（已清除上次的失败标记）")
        log("[完成] 本地估值已刷新 -> assets/valuation.js   （双击 index.html 即见最新 PE/PB）")
    else:
        for x in problems:
            log("[失败] " + x)
        with open(FAIL_MARK, "w", encoding="utf-8") as f:
            f.write("# 本地估值更新失败\n\n")
            f.write("- 时间：%s\n" % t0.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("- 日志：%s\n\n" % logpath)
            f.write("## 问题\n\n")
            for x in problems:
                f.write("- %s\n" % x)
            if warns:
                f.write("\n## 警告\n\n")
                for x in warns:
                    f.write("- %s\n" % x)
            f.write("\n## 怎么处理\n\n")
            f.write("1. 先看上面的日志文件，搜 `[ERR]` 行，那里是真正原因\n")
            f.write("2. 单独重跑抓取：`python scripts\\fetch_valuation.py`\n")
            f.write("3. 若只有中证A500 失败：`python scripts\\calc_a500.py`（首次约 4 分钟，之后秒级）\n")
            f.write("4. 若蛋卷接口临时抽风，隔天通常自愈；连续失败再排查\n")
        log("已写失败标记 -> %s" % FAIL_MARK)
        # 双击运行时弹窗（计划任务无控制台，不弹，靠标记文件与退出码提示）
        if sys.stdout.isatty():
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "本地估值更新失败：\n\n" + "\n".join(problems[:6])
                    + "\n\n详见 logs\\ 目录",
                    "portfolio-board 估值更新", 0x10)
            except Exception:
                pass

    log("日志 -> %s" % logpath)
    with open(logpath, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
