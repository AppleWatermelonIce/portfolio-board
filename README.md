# 持仓净值曲线看板（portfolio-board）

自包含的静态看板。**双击 `index.html` 即可用**（file:// 直开，无需起服务器）。
33 个标的（A股个股 / 港股 / 4 个指数 / 场外基金与 QDII / 黄金 / 美元人民币汇率），
单产品单图，9 个区间标签，收益归一化到起点 0%；指数另附 PE/PB 历史分位底色。

## 目录结构

```
portfolio-board/
├── index.html              ← 站点入口，双击打开
├── assets/                 ← 前端运行资源（页面相对引用，勿改名）
│   ├── data.js             ← 脚本产物：window.PORTFOLIO_DATA
│   ├── valuation.js        ← 脚本产物：window.VALUATION_DATA（指数 PE/PB）
│   ├── live.js             ← 打开页面即自动增量刷新
│   └── vendor/chart.umd.min.js   ← 本地 Chart.js 4.4.3（断网可用）
├── data/
│   ├── products.json       ← ★ 标的清单，唯一真相源（手改这里增删标的）
│   ├── data.json           ← 产物，同 data.js 内容（兼作降级种子）
│   ├── history/*.json      ← 每标的全序列本地缓存（增量更新的基础）
│   └── valuation/*.json    ← 指数 PE/PB 原始序列
├── scripts/
│   ├── update_data.py      ← 主更新脚本（默认增量，--full 全量，--rebuild 离线重建）
│   ├── fetch_valuation.py  ← 抓指数 PE/PB（东财妙想主通道，蛋卷备选）
│   ├── build_valuation_js.py ← 打包 valuation/*.json → assets/valuation.js
│   ├── check_valuation.py  ← 自算分位 vs 官方分位交叉验证（差 <3pp 才采信）
│   └── repair_hfq.py       ← 前复权误用修复（问题已根治，仅作故障备用）
├── tools/                  ← 自检脚本，与运行无关
│   ├── lint_page.js        ← 页面功能点静态检查
│   ├── check_html.js       ← 内联脚本语法 + 33 标的 × 8 区间收益表 + 数据健康检查
│   ├── test_live.js        ← live.js 增量合并仿真
│   ├── browser_test*.js    ← 真实 Chrome（CDP）实测：刷新/节流/估值色带/区间与窗口
│   ├── test_fund_nav.py    ← 基金复权净值口径自测
│   └── _screenshots/       ← 测试截图产物
├── 一键更新.bat            ← 双击重建全历史
├── .gitignore
└── README.md
```

## 用法

**日常**：直接双击 `index.html`。页面加载后 `assets/live.js` 会自动补齐最新数据
（6 小时节流，一天多次打开只拉一次；工具栏「↻ 立即更新」可强刷）。

**重建全历史**（改了 `products.json`、换机器、数据异常时）：双击 `一键更新.bat`，
或命令行：

```bat
python scripts\update_data.py            :: 增量（默认）
python scripts\update_data.py --full     :: 全量重抓
python scripts\update_data.py --rebuild  :: 不联网，仅用 history/ 重合成 data.js
python scripts\update_data.py SH000510   :: 只更新指定 id
```

**刷新估值底色**（PE/PB 分位）：

```bat
python scripts\fetch_valuation.py        :: 抓 4 个指数的 PE/PB 序列 → data/valuation/
python scripts\build_valuation_js.py     :: 打包 → assets/valuation.js（含 5 档分位阈值）
python scripts\check_valuation.py        :: 可选：与蛋卷官方分位交叉验证
```

**自检**：

```bat
node tools\lint_page.js
node tools\check_html.js
node tools\browser_test3.js              :: 估值色带实测（需本机 Chrome）
```

## 关键口径（改动前务必看）

1. **股票/ETF 一律后复权（hfq），禁用前复权**。前复权长周期会出现负价格，
   导致"成立来"收益率荒谬（美的集团会算成 -778%）。
2. **基金用红利再投资复权净值**（nav + unitMoney 递推，含份额折算），不要用 equityReturn 连乘。
3. 黄金人民币价 = 黄金ETF华安(518880) 代理上金所 Au99.99；汇率为欧洲央行每日参考汇率
   （Frankfurter，**必须直连 `api.frankfurter.dev`**，`.app` 的 301 会吃掉 CORS）。
4. **估值分位** = 该值在「最近 N 年（默认 10 年，可切 5 年）」样本内的百分位。
   中证A500 只有 2024-09-02 起 2.1 年数据，10/5 年窗口都取不满，页面会如实标注。
   **多源混用**：沪深300/标普500/纳指100 走**蛋卷**（周频约 515 点），中证A500 走**妙想**
   （日频 501 点）。两源的纳指 PE 有约 8% 口径差（蛋卷 30.96 / 妙想 33.60），
   页面会在徽章悬停与工具栏注明每个标的的来源。估值序列超过 30 天未更新会告警。
5. 5 档配色：低估 0–15% 深绿、较低 15–35% 浅绿、适中 35–70% 黄、较高 70–85% 浅红、高估 85–100% 深红。
6. 红涨绿跌（A股习惯）。

## 数据源

**设计原则：尽量免 key，让服务器也能无人值守地跑。**

| 用途 | 通道 | 需要 key？ |
|---|---|---|
| A股/港股/ETF/指数/黄金 日K | 腾讯 `web.ifzq.gtimg.cn` newfqkline | **否** |
| 场外基金 / QDII 净值 | 天天基金 `pingzhongdata`（JSONP） | **否** |
| 美元/人民币汇率 | Frankfurter (ECB) | **否** |
| 指数 PE/PB：沪深300 / 标普500 / 纳指100 | **蛋卷 danjuanfunds（默认主通道）** | **否** |
| 指数 PE/PB：**中证A500** | 东财妙想 mx-data | **是**（`MX_APIKEY`） |

> **为什么 A500 换不成蛋卷**：蛋卷估值库只有 63 个指数，逐一核对后确认**不收录 000510**，
> `pe_history/SH000510` 返回空数组。A500 是 2024-09 才发布的新指数，免费源普遍没跟上。
> 因此 A500 保留妙想通道；**无 key 时自动沿用上次本地抓取的结果**（页面照常显示，
> 只是不再更新，且超过 30 天会在工具栏告警）。

> **跨源口径差异（如实记录）**：同一时点两源的纳指100 PE 差约 8%（蛋卷 30.96 / 妙想 33.60），
> 标普500 差约 2.5%，沪深300 基本一致。估值分位是按各源自身样本算的，所以同源可比；
> **跨源的绝对 PE 值不要直接横向对比**。页面在每个徽章悬停与工具栏都标了来源。

> 蛋卷历史为**周频约 515 点**（妙想是日频 2597 点）。换源后短区间（近一月）的色带
> 会变成 1–3 个色块，不如日频细腻；分位结论本身不受影响。
> 附带好处：`assets/valuation.js` 从 222KB 降到 57KB。

## 部署到服务器

页面是**纯静态**的，不需要后端，也不需要任何 key 才能"打开看"。

### 方案 A：本地生成 → 同步（推荐）

```bat
:: 本地（有 MX_APIKEY 的机器）
python scripts\update_data.py
python scripts\fetch_valuation.py
python scripts\build_valuation_js.py
```

然后把整个目录（或最小集）同步到服务器即可。服务器零依赖、零 key。

最小集：`index.html` + `assets/`（data.js / valuation.js / live.js / vendor/）。
带 `live.js` 时，浏览器端仍会自动补尾部行情（腾讯/天天基金/Frankfurter 三通道都给 `ACAO:*`）。

### 方案 B：服务器定时重抓（全免 key）

把 `scripts/` 与 `data/` 一起放上去，`pip install requests` 后配 cron（建议北京时间 21:00 后）：

```bash
python scripts/update_data.py              # 行情：三通道全免 key ✓
python scripts/fetch_valuation.py --no-mx  # 估值：蛋卷 3 个 ✓，A500 沿用旧数据
python scripts/build_valuation_js.py
```

**服务器上的唯一缺口**：中证A500 的估值不会更新（缺 `MX_APIKEY`），
会一直沿用上次本地抓取的数据，页面工具栏会提示"妙想项需 MX_APIKEY"
并在超 30 天时告警。要让它也自动更新，三条路：

1. 在服务器配置 `MX_APIKEY`（如果许可允许）；
2. 接受 A500 估值定期手动在本地刷一次再同步；
3. 把 A500 从估值底色的覆盖范围里去掉（删掉 `data/valuation/SH000510.json` 后重跑打包脚本）。

### 注意事项

- `index.html` 在根，相对路径按此结构写死，**不要单独挪动 `assets/` 下的文件名**。
- 脚本里的时间按**北京时间**换算（腾讯/天天基金数据以北京时间为准），服务器时区无所谓。
- `assets/live.js` 的自动刷新依赖浏览器能直连腾讯/天天基金/Frankfurter；
  若部署在纯内网，请改用方案 A 的"本地生成 + 同步"，或关掉页面上的自动刷新。
