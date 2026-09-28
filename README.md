# 持仓净值曲线看板（portfolio-board）

自包含的静态看板。**双击 `index.html` 即可用**（file:// 直开，无需起服务器）。
56 个标的（A股个股 / 港股 / 指数〔含国金中证A500指数增强A〕/ 场外基金与 QDII / 债券基金 / 中国国债收益率 / 黄金 / 美元人民币汇率 / 观察仓），
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
│   ├── fetch_valuation.py  ← 抓指数 PE/PB（蛋卷 3 个 + A500 自算，全免 key）
│   ├── calc_a500.py        ← 中证A500 估值自算：中证官网官方 PE + 自算 ROE（免 key）
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

A500 单独重算（财务结果有缓存，首次约 4 分钟，之后秒级）：

```bat
python scripts\calc_a500.py              :: 生成/更新 A500
python scripts\calc_a500.py --verify     :: 额外用同法算沪深300，与蛋卷官方 PB 对照
python scripts\calc_a500.py --refresh    :: 忽略财务缓存全量重抓
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
   中证A500 只有 2024-09-03 起 2.1 年数据，10/5 年窗口都取不满，页面会如实标注。
   **多源混用**：沪深300/标普500/纳指100 走**蛋卷**（周频约 515 点），
   中证A500 走**中证官网 + 自算 ROE**（日频 501 点）。
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
| 指数 PE/PB：沪深300 / 标普500 / 纳指100 | **蛋卷 danjuanfunds** | **否** |
| 指数 PE/PB：**中证A500** 的 PE | **中证指数官网** `index-perf` 的 `peg` 字段 | **否** |
| 指数 PE/PB：**中证A500** 的 PB | 自算：`PB = PE × ROE`，ROE 由东财 F10 财务算 | **否** |
| 指数 PE/PB（可选对照） | 东财妙想 mx-data（`--mx`） | 是（`MX_APIKEY`） |

### 中证A500 估值的自算方法（免 key，已交叉验证）

蛋卷估值库只有 63 个指数，逐一核对后确认**不收录 000510**（`pe_history/SH000510` 返回空数组）；
中证官网也没有公开的 PE/PB 历史端点。所以按主人给的
`D:\CCWorkspace\.claude\skills\ad_api\scripts\valuation.py` 的公式自行推导：

- **PE**：中证官网 `index-perf` 接口的 `peg` 字段就是官方 **PE(TTM)**。
  已逐日比对验证：沪深300 中证 `peg` 与蛋卷官方 PE 偏差 **0.0%~0.4%**；
  中证500/中证1000 的恒定比例差（−21% / −29%）来自中证「调整股本分级靠档」口径
  ——大盘股靠档到 100% 所以两者一致，小盘股靠档后折算，符合预期。
  覆盖 **2024-09 指数发布至今，日频**（官网更早年份该字段为空）。
- **PB**：没有免费官方端点，用恒等式 `PB = PE × ROE`，
  其中 `ROE = Σ归母净利润(TTM) / Σ归母净资产`（成分整体法）。
  PE 与 PB 的分子同为「Σ(调整股本 × 价格)」，做商后股本口径完全约掉，
  所以 ROE 可以直接用总股本整体法自算再乘官方 PE。
  财报生效按 `REPORT_LAG_DAYS = 30` 天滞后（与 valuation.py 一致）。
- **水平校准**：自算 ROE 与官方隐含 ROE 存在**恒定**系统性偏差（实测 −7.9%，
  源于期末净资产 vs 年报净资产、其他权益工具等口径差）。恒定偏差**不影响分位数**，
  但会影响显示的绝对值，故用蛋卷沪深300 的官方 PB/PE 比值做一次水平校准
  （`k = 官方隐含ROE_300 / 自算ROE_300`）。
- **验证结果**：校准后沪深300 自算 PB = 1.405 vs 蛋卷官方 1.4049（差 **+0.01%**）；
  A500 校准后 ROE = 10.27%，与妙想 A500 隐含 ROE（1.6147/15.7206 = 10.27%）**吻合到 0.01pp**。
  最终 A500 = **PE 14.91 / PB 1.53**，与妙想的 15.72/1.61 差异全部来自 PE 口径，
  我们采用中证官网的权威口径。

> **跨源口径差异（如实记录）**：同一时点不同源的纳指100 PE 差约 8%（蛋卷 30.96 / 妙想 33.60），
> 标普500 差约 2.5%，沪深300 基本一致。估值分位是按各源自身样本算的，所以同源可比；
> **跨源的绝对 PE 值不要直接横向对比**。页面在每个徽章悬停与工具栏都标了来源。

> 蛋卷历史为**周频约 515 点**（妙想是日频 2597 点）。换源后短区间（近一月）的色带
> 会变成 1–3 个色块，不如日频细腻；分位结论本身不受影响。
> 附带好处：`assets/valuation.js` 从 222KB 降到 57KB。

## 部署到服务器

页面是**纯静态**的，不需要后端，也不需要任何 key 才能"打开看"。

### 方案 A：本地生成 → 同步（推荐）

```bat
:: 本地
python scripts\update_data.py
python scripts\fetch_valuation.py
python scripts\build_valuation_js.py
```

然后把整个目录（或最小集）同步到服务器即可。服务器零依赖、零 key。

最小集：`index.html` + `assets/`（data.js / valuation.js / live.js / vendor/）。
带 `live.js` 时，浏览器端仍会自动补尾部行情（腾讯/天天基金/Frankfurter 三通道都给 `ACAO:*`）。

### 方案 B：服务器定时重抓（**4 个指数全部免 key，无缺口**）

把 `scripts/` 与 `data/` 一起放上去，`pip install requests` 后配 cron（建议北京时间 21:00 后）：

```bash
python scripts/update_data.py              # 行情：三通道全免 key ✓
python scripts/fetch_valuation.py          # 估值：蛋卷 3 个 + A500 自算，全免 key ✓
python scripts/build_valuation_js.py
```

A500 通道只依赖**中证官网 + 东财 F10 + 东财基金持仓**三个公开接口，服务器无需任何 key。

> **首次运行会慢一些**：A500 需要抓 500 只成分股 + 300 只沪深300 的财务数据做 ROE，
> 约 **4 分钟**。结果缓存在 `data/valuation/_a500_fin.json` / `_calib_fin.json`
> （已加入 `.gitignore`，不入库），之后每天增量只抓有新报告期的股票，秒级完成。
> 财报季（1/4/7/10 月末）会自动刷新。

### 注意事项

- `index.html` 在根，相对路径按此结构写死，**不要单独挪动 `assets/` 下的文件名**。
- 脚本里的时间按**北京时间**换算（腾讯/天天基金数据以北京时间为准），服务器时区无所谓。
- `assets/live.js` 的自动刷新依赖浏览器能直连腾讯/天天基金/Frankfurter；
  若部署在纯内网，请改用方案 A 的"本地生成 + 同步"，或关掉页面上的自动刷新。
