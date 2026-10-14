# 企业系统接入层 Connector Runtime 设计

> 版本：v1.0（2026-10-14）
> 状态：架构设计；未包含真实连接器实现
> 适用范围：ERP / MES / WMS 等工厂业务系统的只读查询、指标分析与受治理写入
> 相关：`docs/architecture.md` §3.4、§4.3、§9、§11；`docs/research/mvp-plan.md` v1.4

## 1. 结论与边界

本项目需要把“接入层”从协议入口升级为独立的 **Connector Runtime（连接器运行时）**。它位于 LangGraph / 执行层与 ERP、MES、WMS 适配器之间，统一处理连接、鉴权、参数校验、限流、超时、重试、幂等、结果规范化、审计和健康检查。

连接器运行时不承担以下职责：

- 不定义企业业务对象、关系和指标口径；这些由 Ontology / 指标注册表负责。
- 不替代 LangGraph 编排跨系统业务流程；它只执行一个受约束的工具调用。
- 不让 LLM 自由生成 SQL、API URL 或写入参数。
- 不把业务明细写进 Wiki；原始明细留在源系统或受控数据存储，Context 只物化决策所需状态。
- 不在第一阶段引入 CDC、GraphRAG、多源自动合并；这些属于后续规模能力。

## 2. 分层架构

```text
业务 Agent / LangGraph
        │ 统一 Tool Contract
        ▼
Connector Runtime（接入运行时）
  路由 · 凭据引用 · 参数校验 · 策略门
  限流 · 超时 · 重试 · 熔断 · 幂等
  结果规范化 · trace_id · audit · lineage
        │
        ├── REST / WebService Adapter
        ├── SQL Read-only Adapter
        ├── File / Staging Adapter
        └── Event / CDC Adapter（Phase 2）
             │
             ├── ERP instance
             ├── MES instance
             └── WMS instance

Raw Result → Canonical Result → Ontology Mapping → Context Object
                                            │
                              Metric Registry / Deterministic Analysis
                                            │
                                     LLM Explanation
```

核心原则：上层调用“查询生产订单 / 查询库存 / 查询采购订单”等业务工具，不感知底层是 REST、SQL 还是文件；厂商差异收敛在适配器配置、字段映射、错误码映射和工具定义中。

## 3. Connector Registry

连接器实例与凭据分离，生产、测试、开发环境分开注册。配置只保存凭据引用，不保存密码、Token 或 Cookie。

```yaml
connector_id: mes-prod-001
system_type: MES
vendor: custom
environment: production
tenant_id: factory-a
protocol: rest
base_url: ${MES_BASE_URL}
credential_ref: secret://mes-prod-001
capabilities:
  - production_order.query
  - operation_report.query
  - equipment_status.query
data_classification: internal
read_only: true
timeout_ms: 10000
rate_limit_per_minute: 120
status: active
owner: manufacturing-it
```

注册中心至少需要支持：实例启停、版本、环境、负责人、网络区域、凭据引用、能力清单、数据分类、只读标志、限流和有效期。凭据由外部 Secret 管理服务或部署环境注入，禁止进入 Git、日志和审计正文。

## 4. Tool Contract

统一工具契约是上层与接入运行时之间的稳定边界。每个工具声明输入输出、权限、风险、重试和来源。

```yaml
tool_id: mes.production_order.query
connector_id: mes-prod-001
operation: query
risk_level: low
read_only: true
input_schema:
  type: object
  required: [date_from, date_to]
  properties:
    date_from: {type: datetime}
    date_to: {type: datetime}
    workshop_id: {type: entity_ref, entity_type: Workshop}
output_schema:
  type: ProductionOrderResult
permission:
  required_role: production_analyst
  row_scope: workshop
timeout_ms: 10000
retry:
  max_attempts: 2
  retry_on: [timeout, connection_reset]
```

工具契约必须包含：

- `tool_id`、连接器实例和操作类型；
- 输入 / 输出 Schema 与版本；
- 只读标志和风险等级；
- 租户、工厂、车间、库区、时间范围等数据权限范围；
- 超时、重试、熔断和幂等策略；
- 指标口径版本与数据源；
- 脱敏策略、失败分类和审计要求；
- `applicable_domain`（对象类型、状态范围和参数边界）。

工具按能力拆分，不能用一个“万能 ERP 工具”承载所有查询和写入：

```text
erp.purchase_order.query
mes.production_order.query
mes.work_report.query
mes.equipment_status.query
wms.stock.query
wms.stock_adjust.propose
wms.stock_adjust.commit
```

## 5. Adapter 设计

### 5.1 通用适配器

第一阶段建议实现可复用的协议适配器，而不是为每家厂商复制一套运行时：

| Adapter | 作用 | 建议阶段 |
|---|---|---|
| REST / WebService | 调用开放 API，处理认证、分页、错误码和限流 | MVP |
| SQL Read-only | 通过只读账号查询视图或白名单模板 | MVP |
| File / Staging | 读取 CSV、Excel 或中间表，适合过渡期 | 可选 |
| Event / CDC | Kafka、MQ、Webhook、CDC 增量摄入 | Phase 2 |

厂商差异落在配置和映射中：API 路径、分页格式、日期格式、错误码、字段映射、认证方式和业务工具定义。Adapter 不应包含业务指标计算。

### 5.2 SQL Adapter 约束

- 只允许注册过的模板 SQL；LLM 只能绑定参数，不能创建 SQL。
- 参数必须经过类型、枚举、时间范围、Ontology 实体和权限校验。
- 生产默认只读账号；禁止通过查询连接执行 DDL、DML 或存储过程写入。
- 大查询必须有行数、时间范围和超时上限；禁止无条件全表扫描。
- 返回空集、权限拒绝、连接失败、超时、源系统异常必须使用不同错误码，不能把异常伪装成“无数据”。

## 6. 结果分层与数据模型

连接器返回数据分为三层，禁止 Raw Result 直接喂给 LLM 或直接写 Wiki。

### 6.1 Raw Result

保留源系统原始响应或受控引用，用于追溯和重放。至少记录：`trace_id`、源系统、连接器、操作、获取时间、Schema 版本、原始载荷引用和行数。原始数据应脱敏、分区保存并设置保留期。

### 6.2 Canonical Result

把不同系统转换成统一业务结果，例如 `ProductionOrder`、`InventoryBalance`、`PurchaseOrder`。保留源记录 ID、源系统、观察时间、有效时间和映射版本。

### 6.3 Context Object

只有完成 Ontology 映射和可信度校验后，才物化为 Context Object：

```json
{
  "object_type": "ProductionOrder",
  "object_id": "MO-20261014-001",
  "state": "in_progress",
  "properties": {
    "planned_quantity": 1000,
    "completed_quantity": 760,
    "completion_rate": 0.76
  },
  "source": "MES",
  "status": "observed",
  "observed_at": "2026-10-14T10:00:00+08:00",
  "policy_version": "production-metric-v1",
  "trace_id": "trace-example"
}
```

必须保留 `source_value` 与 `effective_value`，并记录 `observed_at`、`received_at`、`effective_from`、`effective_to`、`computed_at`、`trace_id` 和状态（`observed / inferred / confirmed / retracted`）。

## 7. 分析层边界

连接器负责取数，不负责自由分析。分析分为三层：

1. **数据访问层**：查询、分页、参数绑定、路由、原始结果和连接错误。
2. **指标计算层**：按版本化公式做确定性计算。
3. **解释层**：LLM 解释已计算结果、趋势和异常，不改变指标口径。

指标注册示例：

```yaml
metric_id: mes.production.yield_rate
name: 生产良率
formula: qualified_quantity / reported_quantity
unit: percent
dimensions: [workshop, line, product]
time_grain: [day, week, month]
source: [mes.quality_result, mes.work_report]
version: v1
effective_from: 2026-10-01
owner: manufacturing-it
```

第一批建议指标：产量、良率、计划达成率、停机时长、工单延期率、设备利用率、库存可用量和账实差异。每个指标必须可回溯到源工具、源字段、时间范围、公式版本和 `trace_id`。

## 8. 读写分离与可靠性

| 能力 | 示例 | 默认策略 |
|---|---|---|
| Read | 查询订单、库存、产量 | 可自动执行 |
| Calculate | 计算良率、周转率、延期率 | 确定性计算 + 口径版本 |
| Propose | 补货建议、排产建议 | 人工确认 |
| Write | 创建工单、库存调整、回写 ERP | 强制审批 + Saga |

写入链路：

```text
参数校验 → 权限校验 → 业务规则 → dry-run / 预览
→ 人工审批 → 外部调用 → 回查确认 → Context 更新
```

请求超时且外部状态未知、回查失败、部分成功、补偿失败或审计失败时，必须进入 `RECONCILIATION_REQUIRED` / `MANUAL_REVIEW`，不得静默标记成功。写入操作必须有幂等键、持久化 Saga 状态、outbox 事件和逆序补偿。

## 9. ERP / MES / WMS 首批范围

### ERP

优先只读：物料、供应商、采购订单、销售订单、生产订单和账面库存。暂不自动创建采购订单、修改价格、关账或审核财务单据。

### MES

建议作为首个真实试点：生产订单、工单、工序、工位、设备状态、报工、不良品、产量、良率和停机事件。先做只读查询，再验证 Context 物化与指标对账。

### WMS

优先只读：物料库存、库位库存、批次、入库/出库状态和账实差异。库存至少区分 `on_hand / available / allocated / frozen / in_transit / damaged`，不能把账面库存直接当作可用库存。

## 10. 观测、健康检查与对账

连接器运行时需要输出结构化指标：

- 连通性：成功率、超时率、认证失败率、源系统错误率；
- 性能：P50/P95 延迟、分页吞吐、并发和限流次数；
- 数据质量：空值、重复、映射失败、主数据匹配失败；
- 治理：越权拦截、脱敏覆盖、审计完整性；
- 可靠性：重试成功、熔断、回查失败、补偿失败；
- 业务：指标对账差异、Context 延迟、陈旧数据比例。

健康检查必须区分：进程存活、网络可达、认证有效、API/数据库可用、业务查询可用。连接器健康不等于业务数据可信，业务指标仍需和源系统报表对账。

## 11. MVP 落地顺序

### Phase 0：接口与数据盘点

确认系统厂商/版本、接口形态、网络路径、只读账号、数据字典、主数据、指标口径和实时性要求。

### Phase 1：MES 只读连接器

实现一个 MES 实例、一个协议（REST 或 SQL 二选一）和三个查询：生产订单、报工、设备/产线状态。完成 Tool Contract、参数校验、超时错误分类、trace_id、Raw/Canonical/Context 三层结果和只读审计。

### Phase 2：确定性指标分析

增加产量、良率、达成率、停机时长和延期率，建立 Metric Registry 与源系统对账。

### Phase 3：ERP / WMS 复用 Runtime

只新增适配器、工具定义、字段映射、权限域和指标，不复制运行时核心逻辑。

### Phase 4：事件与规模能力

再评估 CDC、消息、Webhook、增量物化、图检索和多源自动合并。

## 12. 验收标准

- 工具可通过统一契约调用，连接器不泄漏厂商协议到上层。
- 非法参数、越权参数、越界时间范围会被拒绝。
- 连接失败、超时、权限拒绝、空结果和源系统错误可区分。
- 每次调用有 `trace_id`、来源、Schema 版本和观察时间。
- 原始结果、标准结果、Context Object 可追溯关联。
- 至少一个 MES 只读查询与源系统人工报表对账一致。
- 指标公式版本化，LLM 不修改公式和 SQL。
- 凭据不进入 Git、日志、审计正文和响应内容。
- 写入路径未完成审批、幂等、回查、Saga 和人工接管前，不得开放生产写权限。

## 13. 待确认信息

在实现前必须确认：

1. ERP、MES、WMS 的厂商、版本和部署方式；
2. 可用接口：REST、WebService、数据库、文件、中间表或消息；
3. Agent 运行环境到工厂内网的网络路径；
4. 只读账号、白名单、VPN、跳板机和审计要求；
5. 首个试点及具体查询指标；
6. 指标口径负责人和源系统对账报表；
7. T+1、小时级还是准实时要求；
8. 第一阶段是否严格只读；
9. Context 是否落现有 SQLite，还是进入独立服务；
10. 是否已有数据仓库作为统一查询出口。
