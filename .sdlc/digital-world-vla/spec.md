# Specification: LLM-Wiki 图谱索引与数字世界 VLA 资产治理

> 来源：已批准意图 `.sdlc/digital-world-vla/intent.md`
> 状态：accepted (2026-10-21)，用户已批准
> 日期：2026-10-21

## 1. 设计结论

LLM-Wiki 作为**资产索引、知识入口和治理投影**，负责让 Agent 找到“应该理解什么、遵守什么、可以调用什么、如何执行以及哪些经验已被验证”。它不替代以下权威系统：

| 内容 | Wiki 中保存 | 权威真源 | Wiki 不负责 |
|---|---|---|---|
| 知识正文与证据 | 摘要、标签、实体关系、来源引用、适用域 | Wiki 文档/受控知识库 + 原始来源 | 保存实时交易明细 |
| Ontology | 类型、属性、关系、版本引用 | Ontology Schema Registry / 受审查 schema 包 | 代替 Context 实例状态 |
| 规则 | 规则说明、版本、优先级、适用域、执行引用 | 规则引擎或确定性策略实现 | 仅凭自然语言授权 |
| 工具 | 能力摘要、输入输出契约、权限和风险元数据 | Connector/Tool Registry | 保存密码、Token 或连接池 |
| Workflow | 目标、触发条件、依赖版本、运行时引用、验收证据 | LangGraph Workflow Registry / 编译定义 | 把自然语言步骤直接当可执行图 |
| 探索图谱 | 候选实体、关系、任务分解、工具发现、轨迹引用、置信度 | Exploration Store + TaskRun/Trace | 自动把候选事实变成正式知识 |
| Context | 当前状态摘要、对象引用、时间和可信状态 | Context Store / 外部业务系统 | 作为实时业务系统替代品 |
| 权限 | 访问策略引用、作用域、风险级别 | Policy/Authorization Service | 自己签发权限 |

核心原则：**Wiki 索引可发现性，Registry 提供可执行权威，Context 提供当前状态，外部业务系统提供事实回执，Policy 服务决定是否允许。**

## 2. 图谱索引模型

### 2.1 顶层节点类型

LLM-Wiki 图谱至少包含以下节点类型。所有节点必须有稳定 `node_id`、`node_type`、`tenant_id`、`owner`、`status`、`version`、`source_refs`、`created_at`、`updated_at` 和 `visibility`。

| 节点类型 | 作用 | 是否可被 Agent 直接使用 |
|---|---|---|
| `Knowledge` | 业务定义、操作知识、FAQ、经验、证据摘要 | 可检索；不得直接产生权限 |
| `Concept` | 术语、指标、业务概念和别名 | 可用于语义解析 |
| `Entity` | 企业对象类型或已确认业务对象引用 | 可用于消歧；实例状态从 Context 读取 |
| `OntologyProfile` | 本体类型、属性、关系和约束版本 | 可用于校验和映射 |
| `Metric` | 指标定义、口径、公式引用、时间语义 | 只能调用已发布版本 |
| `Rule` | 业务规则、策略规则、安全规则 | 依规则类型进入对应校验链 |
| `Policy` | 主体、资源、动作、条件、决策结果和有效期 | 必须由授权服务执行 |
| `Tool` | 数字能力和 Tool Contract 的索引 | 只能调用已注册且授权的版本 |
| `Connector` | ERP/MES/WMS/API/数据库/浏览器等系统连接器 | 只能通过 Connector Runtime 访问 |
| `Workflow` | 已审批固定流程的索引和版本依赖 | 仅可加载 active 版本 |
| `Skill` | 可复用的任务能力说明及输入输出 | 不能绕过工具和策略校验 |
| `Exploration` | 一次未知任务的探索空间和阶段结果 | 仅候选态，不能直接作为事实 |
| `Candidate` | 候选实体、关系、规则、工具映射或 Workflow | 必须人工/规则确认后升级 |
| `ContextRef` | 当前状态、事件、历史或外部对象引用 | 读取时再次鉴权和校验 TTL |
| `Artifact` | 报告、回执、截图、数据集、评估结果 | 作为证据引用，不等于事实本身 |
| `Lesson` | 成功或失败经验、适用条件和反例 | 只能作为受控建议输入 |
| `Evaluation` | 轨迹评估、Golden Case、回归结果和质量指标 | 可影响发布和自主度 |

### 2.2 边类型

边也必须版本化并带来源、置信度、有效期和可见性。建议使用以下受控边，不允许 LLM 任意发明关系名：

| 边 | 含义 |
|---|---|
| `alias_of` | 术语或别名指向概念/实体 |
| `subclass_of` | 类型继承；禁止环 |
| `defines` / `measures` | 概念定义本体或指标 |
| `constrained_by` | 对象、工具或 Workflow 受规则/策略约束 |
| `evidence_for` | 证据支持知识、候选或状态 |
| `applies_to` | 资产适用对象、场景、租户或状态范围 |
| `requires` | 工具、Workflow 或规则依赖某资产 |
| `maps_to` | 源字段/实体映射到标准语义对象 |
| `implemented_by` | Wiki 资产指向 Registry 中的实现 |
| `invokes` | Workflow 节点调用工具或子 Workflow |
| `precedes` / `branches_to` | Workflow 节点控制流关系 |
| `observes` | 工具或连接器观察外部对象/状态 |
| `changes` | 动作可能改变的对象或属性 |
| `verified_by` | 结果由回执、回读或评估验证 |
| `derived_from` | 资产由任务、轨迹或其他来源产生 |
| `supersedes` | 新版本替代旧版本；旧版本不删除 |
| `scoped_by` | 资产受租户、组织、工厂、角色等作用域限制 |
| `related_to` | 弱关联，仅供检索，不能驱动执行 |

边分为三类：`semantic`（语义关系）、`operational`（执行依赖）、`evidence`（证据关系）。只有 `operational` 边通过发布校验后才可影响运行时路由。

### 2.3 索引分片

不要建设一张没有边界的“大图”。建议拆成六个逻辑索引，由统一 Wiki Graph Index 通过稳定 ID 关联：

1. **Semantic Index**：Concept、Entity、OntologyProfile、Metric、alias/subclass/maps_to。
2. **Knowledge Index**：Knowledge、Lesson、Artifact、Evidence、来源和适用域。
3. **Policy Index**：Rule、Policy、权限作用域、风险等级、审批和有效期。
4. **Capability Index**：Tool、Connector、Skill、输入输出 Schema、能力和风险。
5. **Workflow Index**：Workflow、节点、触发器、依赖版本、发布状态、评估和回滚引用。
6. **Exploration Index**：Exploration、Candidate、TaskRun、Trace、ContextRef、待确认缺口。

统一入口只返回当前主体有权看到的节点和边；索引分片不是权限边界，权限仍由 Policy 服务在查询和执行时再次判断。

## 3. 节点公共契约

```yaml
node_id: wf.supply.plan
node_type: Workflow
tenant_id: tenant.factory-a
namespace: factory.supply
version: 1.2.0
status: active
visibility: domain
owner: supply-chain-team
source_refs:
  - kind: approved_spec
    ref: .sdlc/supply-plan/spec.md
applicable_domain:
  object_types: [Product, Material, SalesOrder]
  business_states: [released, shortage_risk]
  parameter_bounds: {horizon_days: {min: 1, max: 90}}
policy_refs: [policy.supply.read@2.0.0]
dependencies:
  ontology: factory.supply@1.1.0
  tools: [tool.wms.shipment.query@1.0.0, tool.erp.order.query@1.2.0]
  rules: [rule.stock.replenishment@1.0.0]
implementation_ref: langgraph://workflow-registry/wf.supply.plan/1.2.0
content_hash: sha256:...
```

强制字段：`status`、`version`、`owner`、`source_refs`、`applicable_domain`、`policy_refs`、`dependencies`、`implementation_ref`（可执行资产）、`content_hash` 和 `lifecycle_events`。

## 4. 知识类型与生命周期

### 4.1 五类知识

| 类别 | 典型内容 | 默认可信状态 | 可否直接驱动动作 |
|---|---|---:|---:|
| 定义知识 | 术语、对象、指标、流程定义 | `confirmed`（须有来源） | 否，先转语义约束 |
| 事实知识 | 已验证的业务状态、历史事件、回执 | `observed/confirmed` | 只能作为 Context 输入 |
| 程序知识 | 工具调用方法、参数绑定、Workflow | `approved/published` | 可，仍需实时授权 |
| 规则知识 | 业务判断、策略、安全限制 | `approved` | 由规则/策略执行器判断 |
| 经验知识 | Lesson、成功轨迹、失败案例、反例 | `candidate/validated` | 只能影响建议，不能改变权限 |

### 4.2 生命周期

普通知识：`draft → extracted → reviewed → approved → published → deprecated → revoked`。

可执行资产：`draft → validated → approved → staged → active → suspended → deprecated/revoked`。

探索资产：`observed → candidate → corroborated → accepted/rejected → promoted/expired`。

禁止通过编辑正文直接改变已发布资产；所有变更生成新版本，旧版本通过 `supersedes` 保留。安全撤销优先于平滑升级；运行中的 TaskRun 在下一动作前重新检查撤销状态。

## 5. 权限模型

### 5.1 四层权限

权限采用“主体—作用域—资产—动作—条件”的统一模型：

```yaml
policy_id: policy.wms.shipment.read
subject: role.supply-planner
scope:
  tenant: tenant.factory-a
  plants: [plant-01]
resource:
  types: [Tool, ContextRef, Artifact]
  ids: [tool.wms.shipment.query]
actions: [discover, read, invoke, export]
conditions:
  data_classification_max: internal
  time_window: business_hours
  approval: none
risk: low
effect: allow
expires_at: 2027-01-01T00:00:00+08:00
```

权限分为：

1. **发现权限**：能否看到节点摘要、名称和存在性。
2. **读取权限**：能否读取正文、参数 Schema、证据和 Context 引用。
3. **执行权限**：能否调用 Tool、Workflow 或 Rule。
4. **发布权限**：能否审批、激活、撤销或回滚资产。

发现权限和读取权限也必须防止敏感信息侧漏；对无权节点可返回 `redacted`，不得通过邻接边推断隐藏资源。

### 5.2 权限决策顺序

`tenant → identity → role/attribute → scope → data classification → asset status/version → action risk → approval condition → decision`。

权限检查至少发生三次：

- 检索时：过滤不可发现节点和边；
- 绑定时：校验当前主体是否能读取输入和调用工具；
- 执行前：重新校验策略版本、有效期、作用域和人工审批引用。

Wiki 中的 `policy_refs` 只是策略引用，不能代替授权服务返回的实时决策。Tool/Workflow 的 `invoke` 权限不自动继承为其子工具权限，必须逐节点检查。

### 5.3 风险分级

| 风险 | 示例 | 默认控制 |
|---|---|---|
| L0 | 静态知识检索 | 自主读取，记录审计 |
| L1 | 只读业务查询 | 自主执行，实时授权，结果核验 |
| L2 | 文件写入、草稿生成、可逆操作 | 事前确认或监督执行 |
| L3 | 跨系统写入、发消息、改订单/排产 | 强制人工审批 + 幂等 + 回读 |
| L4 | 付款、权限变更、不可逆删除 | 双人审批或禁止 Agent 执行 |

## 6. 工具链接与 Connector 索引

### 6.1 分层链接

Wiki 不保存工具实现，只保存以下链路：

`Concept/Entity → Capability → Tool Contract → Connector → External System → Context Object → Verification Artifact`。

Tool 节点至少声明：

- `tool_id/version`、能力摘要、适用域；
- 输入/输出 Schema 和敏感字段；
- `ontology_binding`、`mapping_ref`、`metric_refs`；
- `connector_ref`、外部系统、协议和健康状态；
- `risk_level`、可读/可写动作、幂等键、超时和重试策略；
- 前置条件、后置验证器、失败分类和补偿引用；
- 所需 `policy_refs`、审批类型和审计字段。

### 6.2 工具发现与调用

1. Agent 先按语义、适用域、输入输出类型、数据新鲜度和权限过滤候选 Tool。
2. 仅从 active Tool Registry 读取精确版本，禁止使用 `latest`。
3. 参数只能由用户输入、Ontology 实体、Context 或上游节点按白名单绑定；禁止自由生成 SQL、URL、脚本和凭据。
4. Connector Runtime 执行凭据引用、限流、超时、重试、熔断、幂等、Raw → Canonical → Context 映射和审计。
5. `accepted` 不是成功；`unknown` 必须先回查外部状态；写入动作必须保存业务回执或回读证据。

## 7. Workflow 索引与执行

### 7.1 Workflow 节点

Workflow Index 只登记已编译的 LangGraph 定义和元数据：触发意图、输入输出、节点清单、工具依赖、规则依赖、Ontology/Mapping 版本、权限、适用域、风险、评估结果、人工审批和回滚策略。

Workflow 的运行时权威是 `implementation_ref` 指向的 Workflow Registry；Wiki 负责发现和路由，不直接解释自然语言正文为执行图。

### 7.2 发布门禁

Workflow 进入 `active` 前必须通过：

- DSL/schema 和循环/死路/未处理异常静态检查；
- 工具、规则、Ontology、Mapping、Policy 精确版本冻结；
- 权限、敏感数据和高风险动作审查；
- 正常、越权、审批拒绝、超时、unknown、重复回调和回滚样例；
- 脱敏回放与独立验证集；
- 影子运行或灰度；
- 指定负责人审批并记录 `approval_ref`。

Workflow 运行期间，TaskRun 固定其依赖清单。依赖被撤销时不得继续执行下一副作用动作，转人工处置。

### 7.3 Workflow 与知识的关系

- 知识页解释“为什么”和“业务含义”；
- 规则定义“何时允许/禁止”；
- 工具契约定义“能做什么以及怎样安全调用”；
- Workflow 定义“在批准边界内按什么顺序做”；
- Context 提供“现在是什么状态”；
- Artifact 提供“做完后有什么证据”。

不能用知识页替代 Workflow，不能用 Workflow 替代规则，不能用规则替代实时权限。

## 8. 未知探索图谱

### 8.1 探索对象

未知任务进入 `Exploration`，允许记录：

- 用户目标、输入截图/文本/界面状态引用；
- 候选 Concept/Entity/Relation；
- 候选 Tool、参数绑定和工具发现来源；
- 任务分解、尝试过的路径、失败原因和等待条件；
- Context 查询结果、外部系统回执和验证缺口；
- 可能的 Workflow 草案、规则草案和知识缺口；
- 预算、权限、风险、时间范围和人工检查点。

探索图谱中的节点和边默认 `candidate`，带 `confidence`、`evidence_refs`、`proposed_by` 和 `expires_at`。探索图谱可以指导当前任务的下一步，但不能直接进入正式 Semantic/Capability/Workflow Index。

### 8.2 探索转固化

只有满足以下条件才允许提出 Promotion：

1. 目标和适用域明确；
2. 关键实体和工具映射有可追溯证据；
3. 结果由外部状态、业务回执或人工核对验证；
4. 轨迹没有越权、未审计动作或未处理 unknown；
5. 形成可重复的输入输出和失败处理契约；
6. 通过对应资产的审批、回归和发布门禁。

Promotion 目标必须明确：`Knowledge`、`Rule`、`Tool`、`Workflow` 或 `Ontology/Mapping`，不得把整张探索图谱一键升级。未通过的探索结果保留为 `rejected` 或 `expired`，用于负向经验和评估，不删除。

## 9. 运行时查询链路

```text
用户文本/截图/事件
  -> 意图与作用域解析
  -> Wiki Graph Index 检索（权限过滤）
  -> Semantic/Policy/Capability/Workflow 多索引联合排序
  -> Context 当前状态读取
  -> 命中 active Workflow ? 固定执行 : Exploration 受控规划
  -> Policy 实时授权 + Tool Contract 参数校验
  -> Connector Runtime / Workflow Registry 执行
  -> 外部状态回读或独立验证
  -> TaskRun / Artifact / Context 更新
  -> 结果解释 + 轨迹评估
  -> 候选资产进入审批队列，不直接发布
```

联合排序不能只按向量相似度。最低过滤条件为：租户/作用域、资产状态、适用域、版本可用、权限允许、输入输出兼容、风险可接受。排序信号可包括语义匹配、实体覆盖、历史成功率、数据新鲜度、版本稳定性和用户反馈；历史成功率不能覆盖权限或安全过滤。

## 10. 验收标准

1. 给定同一用户、租户和资产版本，索引查询只返回其有权发现的节点和边。
2. 无读取权限的知识正文、工具参数敏感字段、Context 和 Artifact 不因图邻接关系泄露。
3. 过期、撤销、非 active 或超适用域的 Tool/Workflow 不可直接调用。
4. Tool 调用能追溯到 Tool、Connector、Ontology、Mapping、Policy 和 Workflow 的精确版本。
5. `accepted`、`unknown`、`failed` 与 `succeeded` 在 TaskRun 中保持区分；unknown 不得自动重放。
6. 每个写入动作都具备幂等键、审批引用和外部回执/回读验证；缺任一项不得标记业务完成。
7. 探索图谱中的候选节点不能直接改变正式知识、权限、工具目录或 Workflow 注册表。
8. Promotion 能明确落到单一资产类型，并携带证据、审批、版本和适用域。
9. Workflow 发布能校验全部依赖版本、权限、异常分支和回滚引用。
10. 资产撤销后，新任务和运行中任务的下一副作用动作均被阻断或转人工处置。
11. 20 个以上回放样例覆盖正常、越权、敏感数据、过期资产、审批拒绝、unknown、重复回调、跨租户 ID、版本冲突和探索转固化；任一安全样例失败即阻断发布。
12. 设计文档明确哪些字段是 Wiki 索引、哪些字段属于 Registry/Context/外部系统，避免出现第二个业务事实真源。

## 11. Organization skill constraints

- **Security:** 采用最小权限、租户/组织/工厂作用域隔离、查询和执行双重授权、凭据只存 Secret Manager 引用、参数白名单、敏感字段脱敏、审计 append-only、未知结果禁止盲目重试。
- **Compliance:** 资产和权限变更必须可追溯；保留审批、版本、来源、回执和撤销记录；个人信息、生产经营数据和截图按数据分级、最小化采集和保留期限处理。
- **Data:** 外部业务系统是实时事实来源；Context 是带时间和来源的状态投影；Wiki 不保存交易明细；Ontology/Mapping/Metric/Policy/Tool/Workflow 均精确版本化；历史事实不可被新版本原地覆盖。

## 12. Flagged concerns

- **LLM-Wiki 物理存储未定**：若将 Wiki 同时作为所有资产唯一数据库，可能造成执行代码、权限策略和知识正文生命周期耦合。负责人：技术负责人；建议先采用逻辑统一入口 + 专业 Registry 真源。
- **图数据库能力未定**：Property Graph、RDF 或关系库索引的选择会影响查询、版本和权限实现。负责人：架构负责人；P0 先用受审查清单和确定性索引验证模型。
- **视觉输入边界未定**：截图、DOM、OCR 和视频帧的可信度不同。负责人：产品/安全负责人；未确认前按不可信输入处理，不直接触发高风险动作。
- **策略引擎未定**：业务规则、安全策略和实时授权不能默认由同一模块实现。负责人：安全与平台负责人；先明确 Policy Decision Point 和规则执行边界。
- **首个试点未定**：没有可验证的单一业务闭环就无法校准图谱字段、权限和验收阈值。负责人：业务负责人；建议选择 MES/WMS/ERP 中一个只读或低风险场景。

## 13. Extension contract

- Extension: `LLM-Wiki Graph Index v1`
- Host contract: 现有 Wiki / Ontology / Context / Connector Runtime / Workflow Registry / Policy / TaskRun 契约
- Inputs: 用户任务、身份与作用域、自然语言/视觉引用、Context 查询条件、候选资产检索条件
- Outputs: 授权后的资产索引结果、可执行 Workflow/Tool 引用、探索图谱引用、证据和拒绝原因
- Failures: `UNAUTHORIZED`、`ASSET_NOT_FOUND`、`VERSION_UNAVAILABLE`、`OUT_OF_DOMAIN`、`POLICY_DENIED`、`SCHEMA_MISMATCH`、`EXPLORATION_BUDGET_EXCEEDED`、`VERIFICATION_UNKNOWN`
- Compatibility: 不改变既有 TaskRun/Action 状态含义；不绕过 Connector Runtime、Policy Service、Ontology/Mapping 版本校验；旧 Wiki 条目需通过迁移映射后才能进入 v1 索引。
