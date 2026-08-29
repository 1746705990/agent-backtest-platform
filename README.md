# agent-backtest-platform · A 股回合制回测沙盒

> 一个给 **策略代码生成器（harness / LLM）** 用的 A 股回测沙盒：引擎 + 前端看板 + 策略契约。
> 它本身**不含任何策略、也不内置任何 LLM**。Harness 读规则、现写策略、一键回测、结果实时上墙。

---

## 这是什么

一句话：**你（或你的 AI）写策略代码，平台负责回测并把结果画成 K 线 + 买卖点 + 净值曲线。**

本仓库是一个**策略代码生成沙盒**，定位如下：

- ✅ **引擎**：回合制时钟、撮合、记账、绩效统计（FastAPI 后端）。
- ✅ **前端看板**：K 线图、买卖点标记、净值曲线、"今日操作完毕"按钮。
- ✅ **策略契约**：`agents/strategies/base.py` 暴露一套稳定的 Python 接口，策略只管"今天该买还是卖"。
- ❌ **不含策略**：仓库里不预置任何策略——策略由 harness 根据自然语言需求**现写**后放进 `agents/strategies/`。
- ❌ **不含 LLM**：仓库不连接任何大模型。AI 在仓库之外读 README + `base.py`，生成 `.py` 再交回来跑。

标准工作流：

1. Harness / 开发者读取本文件（项目规则）与 `agents/strategies/base.py`（接口契约）；
2. 根据需求现写一份策略 `.py`，放到 `agents/strategies/`；
3. 用 `run_all.py` 跑回测，结果（K 线 / 买卖点 / 净值曲线）实时显示在前端看板。

---

## 核心特性

- **机制性无未来函数**：看盘阶段屏蔽当日 bar（`date < 今日`），只接受市价单，按**当日开盘价**成交。决策严格基于"昨日及以前"的数据。
- **回合制时钟**：每天策略看盘 → 下单 → 调 `day/finish`（"今日操作完毕"）→ 时钟才推进一天。
- **开箱即用的指标与下单辅助**：`sma / rsi / atr`、按金额 / 权重 / 比例 / 清仓下单，一行调用。
- **公平横向对比**：统一标的池、统一资金（默认 1 万）、统一费用口径，多策略跑批自动出对比表。
- **极简看板**：浏览器打开即可看 K 线、买卖点、净值曲线与基准对比。

---

## 快速开始

### 1. 环境依赖

```bash
pip install -r requirements.txt
# 需要：fastapi, uvicorn, requests  （Python 3.10+）
```

### 2. 配置（环境变量）

| 变量 | 说明 | 默认值 |
|---|---|---|
| `FUYAO_API_KEY` | 扶摇（同花顺）金融数据 API Key——**行情数据源**（非 LLM）。必须通过环境变量提供。 | 无默认值 |
| `SIM_API` | 后端地址，策略客户端（`base.py`）连接用。 | `http://127.0.0.1:8000` |
| `FUYAO_BASE_URL` | 数据 API 基址。 | `https://fuyao.aicubes.cn` |

> ⚠️ **凭据注意**：请通过环境变量注入 `FUYAO_API_KEY`，不要把真实 Key 写入代码或提交到版本库。可参考 `.env.example`。

### 3. 启动后端

```bash
python -m uvicorn server.main:app --port 8000
```

- 看板首页（会话列表 / 默认视图）：http://127.0.0.1:8000/
- 某次回测结果页（K 线 + 买卖点 + 净值）：http://127.0.0.1:8000/?sid=<会话号>
- API 文档（Swagger）：http://127.0.0.1:8000/docs

> 首次访问某标的时，平台会从扶摇自动拉取历史 K 线并落库到 `data/sim.db`（约 300MB+，已在 `.gitignore` 中忽略，可随时重建）。

### 4. 写一个策略

在 `agents/strategies/` 下新建 `.py`，`from base import ...` 即可（接口见下文"策略开发契约"）。仓库已附一份可运行示例：

```bash
# 直接运行示例策略（双均线穿越）
python agents/strategies/example_ma_cross.py --start 2023-06-01 --end 2026-08-01 --cash 10000
```

### 5. 运行回测 & 查看看板

```bash
# 跑一个策略
python agents/strategies/run_all.py example_ma_cross.py \
    --start 2023-06-01 --end 2026-08-01 --cash 10000

# 也可以同时跑多个策略做横向对比
python agents/strategies/run_all.py a.py b.py c.py
```

运行结束会打印绩效表，并给出看板地址 `http://127.0.0.1:8000/?sid=<会话号>`。

> 💡 一次完整回测约 700+ 个交易日，耗时数分钟。长区间建议在后台运行，避免终端超时中断。

---

## 目录结构

```
.
├── server/                 # 后端：FastAPI 引擎 + 看板
│   ├── main.py             # 入口：python -m uvicorn server.main:app
│   ├── engine.py           # 回合制引擎：时钟、撮合、无未来函数约束
│   ├── db.py               # SQLite 连接与建表
│   ├── fuyao_client.py     # 扶摇行情数据客户端
│   └── config.py           # 交易规则 + 数据源配置
├── agents/strategies/      # 策略目录（harness 现写，不预置策略）
│   ├── base.py             # ★ 策略契约：接口 / 指标 / 下单辅助 / 主循环
│   ├── run_all.py          # 通用运行器：跑批 + 横向对比 + 打印绩效
│   └── example_ma_cross.py # 示例策略（双均线穿越，可运行）
├── config.py               # 全局默认参数（回测区间 / 资金 / 默认标的）
├── data/                   # 运行期产物（sim.db 行情缓存，已 gitignore）
├── requirements.txt
└── README.md
```

---

## 策略开发契约

所有策略通过 `agents/strategies/base.py` 与平台交互。一个最小可运行策略需要：

1. `from base import ...` 引入所需工具；
2. 定义 `step(sid, clock, acct, stats) -> str`：用**截至昨日**的数据做决策并**内部完成下单**，返回当日决策说明字符串；
3. 在 `__main__` 用 `cli(...)` 取命令行参数，调用 `run_session(step, name, ...)`。

### 数据 / 下单接口

| 函数 | 作用 | 关键约束 |
|---|---|---|
| `bars(sid, code, limit=250)` | 截至当前模拟日的日 K（升序） | **不含当日 bar** |
| `closes(sid, code, limit=250)` | 收盘价序列 `list[float]` | 同上 |
| `account(sid)` | 资金 + 持仓 + 收益 | 含 `total_value / cash / return_pct / init_cash / positions` |
| `order(sid, code, side, qty)` | 市价单（MARKET），按今日开盘价成交 | `qty` 须为 100 整数倍 |
| `add_log(sid, note)` | 写入当日决策理由 | — |
| `finish(sid, sim_date)` | "今日操作完毕"，推进一天 | 每天**必须**调用一次 |
| `api(path, method, body)` | 底层 HTTP 调用（可直接打 REST） | — |

### 技术指标（已内置，直接复用）

| 函数 | 说明 |
|---|---|
| `sma(vals, n)` | 简单移动平均，返回 `float`（数据不足返回 `None`） |
| `rsi(vals, n=14)` | Wilder 简化版 RSI（0–100） |
| `atr(bs, n=14)` | 真实波幅均值（输入 bar 列表） |
| `highest(bs, n, key="high")` / `lowest(bs, n, key="low")` | 近 n 根最高/最低 |

### 下单辅助（自动整手折算，避免重复造轮子）

| 函数 | 说明 |
|---|---|
| `buy_value(sid, code, amount, cash)` | 按金额买入（不超可用现金） |
| `buy_weight(sid, code, weight, acct)` | 按总资产权重买入 |
| `sell_pct(sid, pos, pct)` | 卖出持仓的 `pct` 比例（不足一手则全卖） |
| `sell_all(sid, pos)` | 清掉可卖部分（受 T+1 约束） |
| `trim_to(sid, pos, want, threshold=0.15)` | 超仓幅度过大则减回目标 |
| `fill_to(sid, code, want, acct, threshold=0.15)` | 欠仓幅度过大则补到目标 |
| `pos_of(acct, code)` / `mv_of(acct, code)` | 取某标的持仓 dict / 市值 |
| `name_of(code)` | 代码 → 中文名 |
| `last_close(sid, code)` | 昨日收盘快照（不含当日） |

### 主循环 & 绩效

| 函数 | 说明 |
|---|---|
| `run_session(step, name, start, end, cash, sid=None, every=1, fill_price="open", thscodes=None)` | 唯一入口：逐日调 `step` 并推钟。返回绩效 dict。`thscodes` 可传自定义标的池（选股策略用），不传则默认 6 宽基 ETF + 国债 ETF。 |
| `cli(desc, every=1)` | 各策略共用命令行参数解析（`--sid/--start/--end/--cash/--fill-price`） |
| `report(sid, name, start, end, seconds, extra="")` | 打印绩效明细（收益/年化/回撤/超额/费用） |
| `bench_return(sid, start, end)` | 同期沪深300ETF 买入持有收益与回撤 |

### 常量（可直接引用）

- `BASE`：后端地址（`SIM_API` 环境变量或默认 `http://127.0.0.1:8000`）
- `CODES`：默认标的池（6 只低相关宽基/商品 ETF）
- `DEFENSIVE` / `NAMES`：防守标的（国债 ETF）/ 代码-名称映射
- `BENCH`：统一基准 `510300.SH`（沪深300ETF）
- `FILL_PRICE` / `current_fill_price()`：当前撮合模式，平台固定为 `"open"`

### 最小模板

```python
# agents/strategies/my_strategy.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_session, bars, order, last_close, cli


def step(sid, clock, acct, stats):
    code = "600519.SH"
    bs = bars(sid, code, 30)          # 截至昨日，不含当日
    if len(bs) < 2:
        return "数据不足"
    # 用昨日及以前的数据算信号，今日开盘价成交（open 模式，无未来函数）
    if bs[-1]["close"] > bs[-2]["close"]:
        price = last_close(sid, code)
        qty = int(acct["cash"] * 0.3 / price / 100) * 100
        if qty >= 100:
            order(sid, code, "BUY", qty)
            return f"看涨，建仓 {qty} 股"
    return "持有不动"


if __name__ == "__main__":
    args = cli("我的策略")
    run_session(step, "我的策略", args.start, args.end, args.cash)
```

### 完整示例（已附带：`example_ma_cross.py`）

双均线穿越 + 分批调仓：

- 标的池：沪深300ETF、黄金ETF、国债ETF（防守垫）
- 用 `sma()` 算快/慢均线；**金叉**把仓位提到上限（单标的 40%），**死叉**降到 0
- 越过 5% 阈值才调仓，避免反复摩擦
- 全程只调用 `buy_weight` / `sell_pct` / `pos_of` 等辅助，市价单按开盘价成交

运行结果（2023-06-01 ~ 2026-07-31，本金 ¥10,000）：

| 指标 | 策略 | 基准（沪深300ETF 买入持有） |
|---|---|---|
| 期末资产 | ¥12,138.14 | — |
| 收益率 | **+21.38%** | +30.45% |
| 年化 | 6.35% | — |
| 最大回撤 | **−7.74%** | −21.95% |
| 超额 | **−9.07%** | — |
| 成交 / 费用 | 115 笔 / ¥683（占本金 6.83%） | — |

> 示例体现的是**机制正确性**：收益跑输指数，但回撤只有约 1/3——典型"低波稳健"特征。策略优劣请自行调参对比。

---

## 无未来函数保证

平台**强制**以下约束，任何会话与下单都无法绕过：

1. **仅 open 模式**：看盘阶段屏蔽当日 bar（`kline` 只返回 `date < 今日`），策略只能看到截至**昨日**的数据。
2. **仅市价单**：下单只接受 `MARKET`，不接受限价单。
3. **撮合价 = 当日开盘价**：市价单按当日开盘价成交。
4. **决策时序固定**：昨日收盘后用截至昨日的数据算信号 → 今日开盘前提交决定 → 今日以开盘价成交 → 今日数据只在明日用于下一次决策。

创建会话时即使传入其它 `fill_price` / 订单 `type`，平台也会强制覆盖为 `open` / `MARKET`。

---

## 交易规则

| 项 | 规则 |
|---|---|
| 成交价 | 当日开盘价（夹在当日 [low, high] 与涨跌停区间内） |
| T+1 | 当日买入次日才可卖 |
| 涨跌停 | 主板 ±10%、创业板(300/301)/科创板(688) ±20%；涨停封死不可买、跌停封死不可卖 |
| 整手 | 买入 100 股整数倍 |
| 佣金 | 万 2.5，最低 5 元 |
| 印花税 | 卖出万 5 |
| 滑点 | 默认 0 |

> ⚠️ 小本金下换手率会被手续费显著惩罚（例如单笔买入 1500 元，实际费率 ≈ 5/1500 = 0.33%，是名义万 2.5 的 13 倍）。这是回测结果的一部分，不是噪声。

---

## REST API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sim/session` | 创建回测会话（固定 open 模式；`thscodes` 可选初始观察池） |
| GET | `/api/sim/sessions` | 查询所有会话（状态 / 成交数 / 最新净值） |
| DELETE | `/api/sim/{sid}` | 删除会话及其交易数据（行情缓存保留） |
| GET | `/api/sim/{sid}/clock` | 时钟状态 |
| GET | `/api/sim/{sid}/kline?thscode=&limit=` | K 线（截至当前日，不含当日） |
| GET | `/api/sim/{sid}/quote?thscode=` | 截至昨日快照 |
| GET | `/api/sim/{sid}/calendar` | 交易日历（由上证指数 000001.SH 生成） |
| POST | `/api/sim/{sid}/orders` | 下单（仅 MARKET，按开盘价成交） |
| GET | `/api/sim/{sid}/orders` | 委托列表 |
| GET | `/api/sim/{sid}/trades` | 成交明细 |
| GET | `/api/sim/{sid}/account` | 资金 + 持仓 + 收益 |
| GET | `/api/sim/{sid}/equity` | 净值曲线 |
| POST | `/api/sim/{sid}/log` | 写当日决策理由 |
| GET | `/api/sim/{sid}/logs` | 决策日志列表 |
| POST | `/api/sim/{sid}/day/finish` | **今日操作完毕**（推进时钟；携带 `sim_date` 防重复提交跳日） |

---

## 配置项说明

**`config.py`（项目根，全局默认参数）**

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEFAULT_START` | `2023-06-01` | 默认回测起点 |
| `DEFAULT_END` | `2026-08-01` | 默认回测终点 |
| `DEFAULT_CASH` | `10000.0` | 默认初始资金（元） |
| `DEFAULT_CODES` | `["600519.SH","000001.SZ"]` | 多股票入口默认观察/交易池 |
| `DEFAULT_STOCK` | `"002009.SZ"` | 单股票策略默认标的 |

**`server/config.py`（交易规则 + 数据源）**

交易规则：`COMMISSION_RATE=0.00025`、`COMMISSION_MIN=5.0`、`STAMP_TAX_RATE=0.0005`、`SLIPPAGE=0.0`、`LOT_SIZE=100`、`LIMIT_PCT_MAIN=0.10`、`LIMIT_PCT_CHINEXT_STAR=0.20`。

数据源：`FUYAO_BASE_URL`、`FUYAO_API_KEY`（见上文"配置"）。

---

## 已知限制

- **复权方式固定为前复权（forward）**，不可切换。
- **ETF 已支持**：查询 A 股接口查不到时自动回退基金接口（如 `510300.SH` 实测可用）；个别冷门基金若上游无覆盖会返回 "Fund not found"。
- **默认策略池**：`run_session` 不传 `thscodes` 时默认 6 宽基 ETF + 国债 ETF；选股类策略请显式传 `thscodes`。REST 层本身对标的开放（任意 A 股代码首次访问即自动拉取落库）。
- 策略约束已全局固定为 open 模式 + 市价单，从机制上杜绝未来函数。

---

## License

内部演示项目，许可证见仓库设置。（如需开源请自行补充 LICENSE。）
