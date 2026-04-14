# 价格与波动率缓存矩阵设计

## 1. 为什么需要矩阵缓存

三类保证金算法都依赖大量重复定价：

- TIMS 需要对 10 个价格场景定价
- Base Risk 需要价格 x 波动率二维场景
- Concentration Risk 需要针对单名标的做更激进的价格/波动率压力测试

如果每次账户重算都逐合约全量调用定价系统，盘中延迟和吞吐量都会很差。因此建议把定价计算拆成：

- `底层矩阵缓存重建`
- `账户级快速聚合`

## 2. 推荐缓存分层

### 2.1 曲线层缓存

- `vol_surface_cache`
- `dividend_curve_cache`
- `rate_curve_cache`

### 2.2 Underlying 场景矩阵层

以 `underlying` 为主键，预先保存各场景下的理论价格、隐含波动率和 Greeks。

建议维度：

- `underlying_id`
- `scenario_family`
  - `TIMS`
  - `BASE_RISK`
  - `CONCENTRATION`
- `scenario_id`
- `expiry_bucket`
- `moneyness_bucket`
- `option_type`
- `exercise_style`
- `version_bundle`

建议输出字段：

- `theo_price`
- `theo_iv`
- `delta`
- `gamma`
- `vega`
- `theta`
- `rho`
- `quality_flag`
- `fallback_flag`

### 2.3 合约级热点缓存

对于交易最活跃、持仓最集中的合约，可以增加更细粒度的合约缓存：

- 精确执行价
- 精确到期日
- 精确交割规格

### 2.4 账户级保证金快照

内容至少包括：

- `account_id`
- `tims_margin`
- `base_risk_margin`
- `concentration_margin`
- `final_margin`
- `dominant_algorithm`
- `version_bundle`
- `calculated_at`

## 3. 版本 Bundle 设计

建议把所有依赖压成一个 `version_bundle`：

```text
version_bundle = {
  market_data_version,
  vol_surface_version,
  dividend_curve_version,
  rate_curve_version,
  scenario_set_version,
  margin_rule_version,
  pricing_model_version
}
```

所有矩阵和快照都必须绑定这个 bundle，保证：

- 可审计
- 可回放
- 可比对

## 4. 不同事件下的重算策略

## 4.1 只变价格，不变曲面

适用场景：

- 盘中小到中等幅度 `spot` 变动
- 波动率报价未明显变化

处理策略：

- 保留原 `vol surface`
- 快速重建受影响 `underlying` 的价格层矩阵
- 优先重算持仓账户

适合的工程实现：

- 只失效 `spot-dependent` 切片
- 不触发完整曲面拟合

## 4.2 价格和波动率都变

适用场景：

- 重大行情波动
- 期权盘口重新定价
- `skew` 和 `wing` 节点明显变化

处理策略：

- 先重建 `vol surface`
- 再重建 Base Risk / Concentration 相关矩阵
- 再重算 TIMS 价格场景

## 4.3 利率或股息变

适用场景：

- 资金利率更新
- 股息预期变化

处理策略：

- 升版对应曲线
- 优先刷新长天期和高敏感桶
- 对短天期低敏感合约可延后补算

## 4.4 公司行动变

适用场景：

- 拆股
- 特别分红
- 并购换股
- 交割规则变更

处理策略：

- 旧矩阵整体失效
- 新旧合约映射单独存档
- 强制全量重建该标的链条

## 4.5 持仓变

适用场景：

- 开仓、平仓、加仓、减仓
- 行权、指派

处理策略：

- 直接账户级重算
- 如果 `underlying` 缓存缺失，则补建对应热矩阵
- 不应把账户事件放大成全市场矩阵重算

## 5. 因子变化对不同期权的影响

| 变化因子 | 影响更大的期权 | 主要原因 | 风控提示 |
| --- | --- | --- | --- |
| `Spot` 大跳 | 临近到期近平值、跨执行价的原深虚值期权 | `gamma` 高，价格非线性强 | 关注卖方短 gamma 账户 |
| `ATM Vol` 变化 | 长天期近平值期权 | `vega` 最大 | Base Risk 对其更敏感 |
| `Skew/Wing` 变化 | 深度虚值认沽、远端翼侧期权 | 尾部外推变化大 | Concentration Risk 应重点覆盖 |
| 利率变动 | 长天期、期货期权、rho 高合约 | 贴现与远期价格变化 | 可以只优先补算长天期切片 |
| 股息变动 | 单股票美式看涨、临近除息深实值看涨 | 提前行权边界变化 | 股息版本要进入快照 |
| 公司行动 | 所有调整交割合约 | 合约规格变化而非仅参数变化 | 必须整体切换版本 |
| Offset 规则变动 | 互抵比例高的价差与组合 | 聚合逻辑改变 | 保证金解释要展示“规则版号” |

## 6. 建议的数据分区方式

为了让系统可水平扩展，建议：

- 按 `underlying_id` 做主分区
- 按 `scenario_family` 做次分区
- 按 `valuation_date` 或 `market session` 做版本归档

账户快照则建议：

- 按 `account_id hash` 分区
- 再按 `calculated_at` 排序

## 7. 热点预热策略

建议每天开盘前和盘中定时维护“热标的池”：

- 持仓最集中前 `N` 个标的
- 当天成交最活跃前 `N` 个标的
- 风险敞口最大的前 `N` 个标的

这些标的优先常驻内存缓存。

## 8. 失效与回收策略

- `spot` 小幅变化：局部失效
- `vol surface` 变化：失效相关 `underlying + expiry/wing` 切片
- 公司行动：整条标的链全失效
- 新版本稳定后，旧版本缓存延后回收，方便审计回放

## 9. 工程实现建议

- 热缓存：`Redis` 或内存 KV
- 冷存储：对象存储或列式数据库
- 快照检索：`ClickHouse` 或高性能时序/分析库
- 配置与版本：关系型数据库

## 10. 最重要的落地原则

- 不要把所有事件都做成全量重算。
- 不要把矩阵缓存和账户快照混成一个层次。
- 不要让快照脱离版本信息。
- 不要忽略公司行动对单股票美式期权的影响。
