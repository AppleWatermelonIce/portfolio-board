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
5. 5 档配色：低估 0–15% 深绿、较低 15–35% 浅绿、适中 35–70% 黄、较高 70–85% 浅红、高估 85–100% 深红。
6. 红涨绿跌（A股习惯）。

## 数据源

| 用途 | 通道 | 需要 key？ |
|---|---|---|
| A股/港股/ETF/指数/黄金 日K | 腾讯 `web.ifzq.gtimg.cn` newfqkline | 否 |
| 场外基金 / QDII 净值 | 天天基金 `pingzhongdata`（JSONP） | 否 |
| 美元/人民币汇率 | Frankfurter (ECB) | 否 |
| 指数 PE/PB 长序列 | 东财妙想 mx-data | **是**（`MX_APIKEY`） |
| 指数估值交叉验证 | 蛋卷 danjuanfunds | 否 |

> 妙想的 key 缺失时，`fetch_valuation.py` 会退回蛋卷通道，但**蛋卷不收录中证A500**，
> 且只有约 515 个样本点。要 10 年级分位请配 `MX_APIKEY`。

## 部署

整个目录拷走即可。静态托管（GitHub Pages / Nginx / 对象存储）直接把目录内容作为站点根；
`index.html` 在根，相对路径已按此结构写死，**不要单独挪动 `assets/` 下的文件名**。

若只想部署、不想带缓存与脚本，最少需要：`index.html` + `assets/` 三个 js + `assets/vendor/`。
（这样部署后不再自动刷新尾部数据，需本地跑完脚本再同步。）
