# LLM-Wiki 图谱索引与数字世界 VLA 资产治理设计

> 版本：v1.0（2026-10-21）
> 状态：设计契约；已完成意图、规范和实施计划审批；尚无运行时实现或生产验证
> 关联：[主架构](../architecture.md) §3.6、[数字世界 VLA intent](../../.sdlc/digital-world-vla/intent.md)、[数字世界 VLA spec](../../.sdlc/digital-world-vla/spec.md)

## 1. 定位与权威边界

LLM-Wiki 是数字世界 Agent 的**统一资产索引、知识入口和治理投影**。它把知识、规则、工具、Workflow、探索结果和证据组织成可发现、可追溯、可审查的图谱，但不把所有内容混成一个运行时真源。

| 内容 | Wiki 索引 | 权威真源 |
|---|---|---|
| 知识正文与证据 | 摘要、标签、关系、来源和适用域 | Wiki 知识库与原始来源 |
| Ontology | 类型、关系和版本引用 | Ontology Schema Registry / schema 包 |
| 规则 | 规则说明、优先级、适用域和实现引用 | 规则引擎或确定性策略实现 |
| 权限 | 策略引用、作用域和风险级别 | Policy/Authorization Service |
| 工具 | 能力、契约、输入输出、风险和连接关系 | Tool Registry / Connector Runtime |
| Workflow | 目标、触发条件、依赖版本和运行时引用 | Workflow Registry / LangGraph 编译定义 |
| Context | 当前状态摘要、对象和证据引用 | Context Store / 外部业务系统 |
| 探索图谱 | 候选实体、关系、工具、轨迹和缺口 | Exploration Store + TaskRun/Trace |
| 执行证据 | 回执、回读、截图、评估和审计引用 | Artifact/Audit Store |

原则：**Wiki 索引可发现性，Registry 提供可执行权威，Context 提供当前状态，外部业务系统提供事实回执，Policy 服务决定是否允许。**

## 2. 图谱模型

### 2.1 节点类型

节点公共字段至少包括：

```yaml
node_id: wf.supply.plan
node_type: Workflow
tenant_id: tenant.factory-a
namespace: factory.supply
version: 1.2.0
status: active
visibility: domain
owner: supply-chain-team
source_refs: []
applicable_domain: {}
policy_refs: []
dependencies: {}
implementation_ref: langgraph://workflow-registry/wf.supply.plan/1.2.0
content_hash: sha256:...
```

核心节点：

| 类型 | 用途 | 运行时边界 |
|---|---|---|
| `Knowledge` | 定义、FAQ、操作知识、证据摘要 | 可检索，不能直接授权 |
| `Concept` | 术语、指标概念、别名 | 参与语义解析 |
| `Entity` | 对象类型或已确认对象引用 | 实例状态从 Context 读取 |
| `OntologyProfile` | 类型、属性、关系和约束 | 用于映射与校验 |
| `Metric` | 口径、公式引用、时间语义 | 只能调用已发布版本 |
| `Rule` | 业务、策略和安全规则 | 进入对应规则执行链 |
| `Policy` | 主体、资源、动作、条件和有效期 | 必须由授权服务执行 |
| `Tool` | Tool Contract 能力索引 | 必须实时授权后调用 |
| `Connector` | API、数据库、ERP/MES/WMS、浏览器连接器 | 只能经 Connector Runtime |
| `Workflow` | 固定流程和依赖版本索引 | 仅 active 版本可路由 |
| `Skill` | 可复用任务能力说明 | 不得绕过策略和工具校验 |
| `Exploration` | 未知任务探索空间 | 默认候选态 |
| `Candidate` | 候选实体、关系或资产 | 需确认后 Promotion |
| `ContextRef` | 当前状态、事件、历史引用 | 读取时再次鉴权和检查 TTL |
| `Artifact` | 报告、回执、截图、评估结果 | 作为证据引用 |
| `Lesson` | 成功/失败经验和反例 | 只影响建议，不改变权限 |
| `Evaluation` | 轨迹、黄金集和回归结果 | 可影响发布和自主度 |

### 2.2 受控边

边必须带版本、来源、置信度、有效期和可见性，不允许模型任意发明关系名。受控关系包括：

- 语义：`alias_of`、`subclass_of`、`defines`、`measures`、`maps_to`；
- 约束与作用域：`constrained_by`、`applies_to`、`scoped_by`、`requires`；
- 执行：`implemented_by`、`invokes`、`precedes`、`branches_to`、`observes`、`changes`；
- 证据与版本：`evidence_for`、`verified_by`、`derived_from`、`supersedes`；
- 弱关联：`related_to`，只能用于检索，不能驱动执行。

边分为 `semantic`、`operational` 和 `evidence` 三类。只有通过发布校验的 `operational` 边才能影响 Agent 路由。

### 2.3 六类逻辑索引

统一 Graph Index 通过稳定 ID 关联六类逻辑索引，逻辑分片不是权限边界：

1. **Semantic Index**：Concept、Entity、OntologyProfile、Metric、别名、继承、映射。
2. **Knowledge Index**：Knowledge、Lesson、Artifact、证据、来源、适用域。
3. **Policy Index**：Rule、Policy、作用域、风险、审批、有效期。
4. **Capability Index**：Tool、Connector、Skill、Schema、能力和风险。
5. **Workflow Index**：Workflow、节点、触发器、依赖、发布状态、评估、回滚。
6. **Exploration Index**：Exploration、Candidate、TaskRun、Trace、ContextRef、待确认缺口。

## 3. 知识类型与生命周期

| 类别 | 内容 | 默认状态 | 能否直接驱动动作 |
|---|---|---|---|
| 定义知识 | 术语、对象、指标、流程定义 | confirmed（需来源） | 否，转为语义约束 |
| 事实知识 | 业务状态、历史事件、业务回执 | observed/confirmed | 只能作为 Context 输入 |
| 程序知识 | 工具方法、参数绑定、Workflow | approved/published | 可，但仍需实时授权 |
| 规则知识 | 业务判断、策略、安全限制 | approved | 由规则/策略执行器判断 |
| 经验知识 | Lesson、坏例、成功轨迹、反例 | candidate/validated | 只提供受控建议 |

生命周期：

- 普通知识：`draft → extracted → reviewed → approved → published → deprecated/revoked`；
- 可执行资产：`draft → validated → approved → staged → active → suspended → deprecated/revoked`；
- 探索资产：`observed → candidate → corroborated → accepted/rejected → promoted/expired`。

已发布资产不原地覆盖；新版本通过 `supersedes` 保留历史。安全撤销必须在运行中任务下一副作用动作前阻断或转人工。

## 4. 权限索引与授权

权限统一表达为“主体—作用域—资产—动作—条件”：

```yaml
policy_id: policy.wms.shipment.read
subject: role.supply-planner
scope: {tenant: tenant.factory-a, plants: [plant-01]}
resource: {types: [Tool, ContextRef, Artifact], ids: [tool.wms.shipment.query]}
actions: [discover, read, invoke, export]
conditions: {data_classification_max: internal, approval: none}
risk: low
effect: allow
expires_at: 2027-01-01T00:00:00+08:00
```

四层权限：

1. **发现**：能否看到节点摘要和存在性；
2. **读取**：能否读取正文、Schema、证据和 Context 引用；
3. **执行**：能否调用 Tool、Workflow 或 Rule；
4. **发布**：能否审批、激活、撤销和回滚资产。

权限决策顺序：`tenant → identity → role/attribute → scope → data classification → asset status/version → action risk → approval condition`。

权限至少检查三次：

- 检索时过滤不可发现节点和边；
- 参数绑定时检查输入和工具读取权限；
- 执行前重新检查策略版本、有效期、作用域和审批引用。

风险等级：L0 静态检索，L1 只读查询，L2 可逆写入，L3 跨系统写入，L4 付款、权限变更、不可逆删除。L3 默认强制人工审批，L4 默认禁止 Agent 自主执行。

## 5. 工具链接

标准链路：

```text
Concept/Entity
  -> Capability
  -> Tool Contract
  -> Connector
  -> External System
  -> Context Object
  -> Verification Artifact
```

Tool Contract 至少声明：工具版本、能力摘要、适用域、输入输出 Schema、敏感字段、Ontology/Mapping/Metric 绑定、Connector 引用、外部系统、风险、幂等键、超时、失败分类、后置验证器、Policy 引用和审批类型。

调用顺序：

1. 按语义、适用域、Schema、数据新鲜度和权限过滤候选 Tool；
2. 从 active Tool Registry 读取精确版本，禁止 `latest`；
3. 仅按白名单绑定用户输入、Ontology 实体、Context 和上游节点输出；
4. 由 Connector Runtime 处理凭据引用、限流、超时、重试、熔断、幂等、Raw → Canonical → Context 映射和审计；
5. `accepted` 只能表示已接收，`unknown` 必须回查外部状态；写入必须有外部回执或回读证据。

禁止自由生成 SQL、URL、脚本或凭据。业务明细不写入 Wiki。

## 6. Workflow 索引与发布

Wiki 的 Workflow 节点只保存触发意图、输入输出、适用域、节点摘要、工具/规则依赖、Ontology/Mapping/Policy 版本、风险、评估结果、审批引用、回滚引用和 `implementation_ref`。运行时权威是 Workflow Registry 中冻结版本的 LangGraph 定义。

进入 active 前必须通过：

- DSL/Schema、循环、死路和异常分支静态检查；
- 工具、规则、Ontology、Mapping、Policy 精确版本冻结；
- 权限和敏感数据审查；
- 正常、越权、审批拒绝、超时、unknown、重复回调和回滚回放；
- 影子运行或灰度；
- 指定责任人审批。

TaskRun 固定依赖清单，禁止解析 `latest`。Workflow 编排成功不等于业务成功。外部写入必须具备幂等键、审批引用和业务回执/回读验证。

## 7. 未知探索与 Promotion

Exploration 可以记录：用户目标、文本/截图/界面状态引用、候选实体和关系、候选 Tool、任务分解、尝试路径、失败原因、Context 结果、外部回执、验证缺口、Workflow 草案和预算边界。

所有探索节点和边默认 `candidate`，必须带 `confidence`、`evidence_refs`、`proposed_by`、`expires_at`。探索可以影响当前任务规划，但不能直接改写正式索引或授权。

Promotion 需要：目标和适用域明确、关键映射有证据、外部状态/回执/人工核对完成、轨迹无越权和未处理 unknown、输入输出/失败契约可重复，并通过对应资产审批和回归。Promotion 必须落到单一类型：`Knowledge`、`Rule`、`Tool`、`Workflow` 或 `Ontology/Mapping`；不能整图一键升级。

失败探索保留为 `rejected` 或 `expired`，用于负向 Lesson 和评估，不删除。

## 8. 数字世界 VLA 运行时链路

```text
文本/截图/界面事件
  -> 意图与作用域解析
  -> Graph Index 权限过滤
  -> 六类索引联合检索
  -> Context 当前状态读取
  -> 命中 active Workflow ? 固定执行 : Exploration 受控规划
  -> 实时 Policy 授权 + Tool 参数校验
  -> Connector/Workflow 执行
  -> 外部状态回读或独立验证
  -> TaskRun / Context / Artifact 更新
  -> 解释输出 + 轨迹评估
  -> 候选资产进入审批队列
```

视觉输入（截图、OCR、DOM、视频帧）默认是不可信输入，不得直接触发 L3/L4 动作。模型总结只能是解释性 Artifact，不能作为业务成功证据。

## 9. 验收矩阵

| 验收项 | 文档契约 | 运行时状态 |
|---|---|---|
| 权限过滤与三次检查 | 已定义 | 待实现验证 |
| 六类索引与节点/边类型 | 已定义 | 待选存储和实现 |
| Tool 精确版本绑定 | 已定义 | 待 Connector 实现 |
| Workflow 依赖冻结与发布门禁 | 已定义 | 待 Registry 实现 |
| unknown 回查和外部验证 | 已定义 | 待运行时回放 |
| 探索候选隔离与 Promotion | 已定义 | 待探索存储实现 |
| 20+ 正负例回放 | 验收要求已定义 | 尚未执行 |

P0 建议选择一个 ERP/MES/WMS 的低风险只读业务闭环，先用受审查清单和确定性索引验证模型；在接口、账号、数据字典和业务负责人确认前，不声称真实连接成功。

## 10. 未决事项

- Wiki 是逻辑统一入口还是全部资产的唯一物理存储；建议 P0 采用逻辑入口 + 专业 Registry 真源。
- 图存储选型（Property Graph、RDF 或关系库索引）。
- 业务规则、安全策略和实时授权是否分别采用不同执行器。
- 首个数字业务场景、视觉输入格式和低风险动作范围。
- 目标部署形态、审计留存、数据分级和租户隔离指标。

## 11. Extension Contract

- Extension: `LLM-Wiki Graph Index v1`
- Inputs: 用户任务、身份/作用域、自然语言或视觉引用、Context 查询条件、候选资产检索条件。
- Outputs: 授权后的索引结果、Workflow/Tool 引用、探索图谱引用、证据和拒绝原因。
- Failures: `UNAUTHORIZED`、`ASSET_NOT_FOUND`、`VERSION_UNAVAILABLE`、`OUT_OF_DOMAIN`、`POLICY_DENIED`、`SCHEMA_MISMATCH`、`EXPLORATION_BUDGET_EXCEEDED`、`VERIFICATION_UNKNOWN`。
- Compatibility: 不改变既有 TaskRun/Action 状态含义；不绕过 Connector Runtime、Policy Service、Ontology/Mapping 版本校验；旧 Wiki 条目需迁移后才能进入 v1 索引。

## 12. 后续 TODO：决策门控层（Decision Gate / AgentJev）

> 状态：**TODO，未设计、未实现、未纳入本期范围**。主架构占位见 [architecture.md §3.7](../architecture.md)。本节只记录待定项与约束，不构成方案；在立项前，以下任何结论都不得作为实施依据。

### 12.1 待定项

| 待定项 | 当前状态 | 说明 |
|---|---|---|
| 是否引入独立决策模型 | 未定 | 备选是"确定性规则 + 大模型结构化输出"，未必需要单独模型 |
| 决策问题划分 | 未定 | 路由 / 门控 / 风险分级 / 结果质量，四类是否都做、先做哪类 |
| 候选集来源与过滤顺序 | 未定 | 由 Capability Index 与 Workflow Index 生成，先过滤后判断的顺序需验证 |
| `DecisionPolicy` 字段 | 未定 | 阈值、`margin`、适用域、降级链、模型版本如何绑定 |
| `DecisionArtifact` 字段 | 未定 | 概率、候选、最终结果、责任人、回放引用 |
| 与三次授权检查的叠加关系 | 未定 | 决策层在授权前还是后，如何避免重复或互相绕过 |
| 是否领域训练 | 未定 | 须先有真实任务回放集与概率校准能力才能判断 |
| 错误归因与责任边界 | 未定 | 决策错误归模型、候选集、数据还是审批链 |

### 12.2 约束（若将来实施）

- 候选集不能在模型侧扩展；模型不创造候选，也不改写候选。
- 概率不能替代外部回执、状态回读或人工闸门；`unknown` 一律先回查。
- L3/L4 风险动作不因置信度高而自动放行。
- 可判定规则（金额、客户等级、齐套率、权限作用域）留在 Policy / Rule，不进模型。
- 模型不可用时降级到确定性流程或人工，不改变 TaskRun/Action 状态语义。
- 每次决策留存可回放的决策证据，支持校准与归因。

### 12.3 启动条件

在以下条件同时满足前，本项保持 TODO：

1. 六类索引可被真实查询，且权限过滤在索引层已生效。
2. Policy Service 与 Tool Registry 有可运行实现，能做实时授权与精确版本绑定。
3. 已有真实任务回放集与基线指标（工具选择准确率、误放行率、误拦截率）。
4. 已选定单一业务决策场景并明确责任人。

在此之前，任何关于"决策模型能提升多少准确率"的说法都缺乏可验证依据。
