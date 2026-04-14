# Industrial Margin Engine

这是一个可本地运行的工业级保证金系统演示版，覆盖：

- `TIMS` 保证金算法
- `Base Risk` 保证金算法
- `Concentration Risk` 保证金算法
- 事件驱动的曲线/矩阵重算
- 账户级保证金快照与砍仓告警
- 可扩展的风险算法与配置变更框架

最终保证金结果为：

```text
Final Margin = max(TIMS Margin, Base Risk Margin, Concentration Risk Margin)
```

## 现在已经可以做什么

当前版本已经是一个“可运行演示系统”，不是只读设计稿：

- 可以启动本地 HTTP 服务
- 可以装载 demo 市场数据与 demo 持仓
- 可以通过事件驱动重算矩阵与账户保证金
- 可以查询账户快照、持仓和场景矩阵
- 可以模拟市场冲击、利率变动、股息变动、配置变更、公司行动、持仓变化

## 目录

```text
industrial_margin_engine/
├── README.md
├── demo_flow.py
├── run_demo.py
├── configs/
├── docs/
├── pyproject.toml
├── schemas/
└── src/
    └── margin_engine/
        ├── __init__.py
        ├── algorithms.py
        ├── domain.py
        ├── orchestrator.py
        ├── policies.py
        ├── pricing.py
        ├── runtime.py
        ├── server.py
        ├── store.py
        └── utils.py
```

## 快速启动

在项目根目录运行：

```bash
/Users/bai/Documents/Playground/.venv/bin/python run_demo.py --host 127.0.0.1 --port 8010
```

启动后可直接访问：

- `GET /health`
- `GET /accounts`
- `GET /accounts/ACC10001/snapshot`
- `GET /accounts/ACC20001/portfolio`
- `GET /underlyings`
- `GET /underlyings/TSLA/matrix?family=CONCENTRATION`

## 也可以直接跑命令行 demo

```bash
/Users/bai/Documents/Playground/.venv/bin/python demo_flow.py
```

这个脚本会：

1. 重置 demo 数据
2. 打印初始快照
3. 注入一次 `TSLA` 市场冲击事件
4. 输出冲击后的重算结果和新快照

## 示例 API

### 1. 重置 demo 数据

```bash
curl -X POST http://127.0.0.1:8010/demo/reset
```

### 2. 查询保证金快照

```bash
curl http://127.0.0.1:8010/accounts/ACC20001/snapshot
```

### 3. 注入市场冲击事件

```bash
curl -X POST http://127.0.0.1:8010/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "MARKET_SHOCK",
    "priority": "P0",
    "scope": "UNDERLYING",
    "source": "manual-test",
    "underlyings": ["TSLA"],
    "payload": {
      "spot_move_pct": -0.12,
      "iv_move_abs": 0.10,
      "reason": "stress test"
    }
  }'
```

### 4. 替换账户持仓并自动重算

```bash
curl -X POST http://127.0.0.1:8010/positions/replace \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "ACC30001",
    "cash_balance": 100000,
    "positions": [
      {
        "position_id": "spy_short_put",
        "underlying": "SPY",
        "instrument_type": "OPTION",
        "quantity": -20,
        "multiplier": 100,
        "class_group": "SPY",
        "product_group": "US_INDEX_OPTIONS",
        "portfolio_group": "US_EQUITY_DERIVATIVES",
        "option_right": "PUT",
        "strike": 480,
        "days_to_expiry": 35
      }
    ]
  }'
```

## 已实现的事件触发逻辑

- `MARKET_SHOCK`
  - 更新受影响标的的 `spot/base_vol`
  - 大波动时重算波动率曲面版本
  - 重建相关 `price/vol matrix`
  - 重算受影响账户并输出快照
- `VOL_SURFACE_CHANGED`
  - 更新波动率并重建矩阵
- `RATE_CURVE_CHANGED`
  - 更新利率并重建矩阵
- `DIVIDEND_CURVE_CHANGED`
  - 更新股息率并重建矩阵
- `MARGIN_CONFIG_CHANGED`
  - 更新场景与 offset 配置并重算
- `OFFSET_MAPPING_CHANGED`
  - 更新组间 offset 配置并重算
- `CONCENTRATION_CONFIG_CHANGED`
  - 更新集中度参数并重算
- `CORPORATE_ACTION_EFFECTIVE`
  - 支持演示版 `split_ratio` 与 `cash_dividend`
  - 自动调整相关股票/期权仓位参数
- `POSITION_CHANGED`
  - 直接触发账户级重算

## 当前实现里的关键工程点

- 使用标准库 `http.server`，不依赖额外安装
- 使用内存态 `ArtifactStore`
- 使用版本化 `VersionBundle`
- 使用价格/波动率场景矩阵：
  - `TIMS`: 10 个等距价格点
  - `BASE_RISK`: 价格 x 波动率二维场景
  - `CONCENTRATION`: 单名强压力场景
- 使用三层聚合逻辑：
  - `Class Group`
  - `Product Group`
  - `Portfolio Group`
- 使用账户快照输出：
  - `tims_margin`
  - `base_risk_margin`
  - `concentration_margin`
  - `final_margin`
  - `dominant_algorithm`
  - `margin_utilization`
  - `liquidation_required`

## 演示版假设

为了做到“今天就能运行”，当前定价和风险实现做了简化，但接口和触发链路是完整的：

- 期权定价使用简化版 `Black-Scholes`
- 美式期权通过轻量提前行权溢价近似
- `Concentration Risk` 在强压力场景基础上额外加入集中度附加系数
- 公司行动目前主要演示 `split_ratio` 和现金分红冲击
- 缓存矩阵目前存的是场景化 `spot/vol/rate/dividend` 点阵，便于后续替换成更细的合约级理论价缓存

## 推荐阅读顺序

1. [系统总体架构](./docs/01_architecture.md)
2. [事件触发矩阵](./docs/02_event_trigger_matrix.md)
3. [价格与波动率缓存矩阵设计](./docs/03_pricing_cache_design.md)
4. [落地分期方案](./docs/04_rollout_plan.md)

## 后续如果继续增强

下一步最值得补的是：

- 接入真实消息总线，例如 `Kafka`
- 把内存存储替换成 `Redis + ClickHouse/Postgres`
- 引入更细粒度的合约级理论价缓存
- 增加 `Liquidity Risk`、`Dislocation Risk`
- 加入异步任务队列和批量并发重算
