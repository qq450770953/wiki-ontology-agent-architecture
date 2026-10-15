# Ontology Playground 融合设计与兼容性审查

> 版本：v1.0（2026-10-15）
> 状态：设计契约；尚无运行时代码、构建或端到端验证
> 关联：[主架构](../architecture.md) §3.5；[既有本体工作台](ontology-construction-design.md) M1–M4；[连接器](connector-runtime-design.md)

## 1. 审查结论：保留、调整与补充

| 决策 | 内容 | 原因 |
|---|---|---|
| 保留 | 受约束模型、图预览、实时校验、版本目录、人工审查 | 与当前类型化语义层和治理原则一致 |
| 调整 | 不新造 Workbench、Ontology Registry 或审批系统 | 复用既有工作台 M1–M4、版本化 schema 和审批审计；Catalogue 只是投影 |
| 调整 | 可视化编辑、自动影响分析、RDF 导入导出后置 | 当前优先级是一个真实业务闭环，不是完整前端产品 |
| 调整 | 不声称与 ontology-enterprise 的配置直接兼容 | 当前未检出和测试外部实现；字段映射需另做适配验证 |
| 补充 | 固定版本依赖、对象身份、字段映射、错误分类、迁移和负例 | 原报告不足以作为实现契约 |

不直接 fork Playground 作为 Runtime，不改变八层职责，不新增部署层。当前仓库只维护设计；运行时代码须在已确认的独立实现仓落地。

## 2. 来源和证据边界

参考微软官方 [Ontology-Playground](https://github.com/microsoft/Ontology-Playground) 与其 README（2026-10-15 调研）。其定位是 Preview 本体学习与可视化设计工具：静态前端优先、React/TypeScript、Cytoscape 图预览、目录、设计器、受限 RDF/XML 互操作，可选 API 与静态前端解耦。

仅吸收模式，不将 README 功能声明视为本地测试结果，也不固定其浮动 main 为依赖。若复用源码，必须先固定 commit，检查实际依赖的 Node engine、许可证和安全问题，再在独立环境构建测试。此前报告中关于具体导出 profile 的描述，在复用前须核对固定 commit 的源码和往返测试；不得预设全部 OWL/SHACL 语义可保真。

本项目 README 明确是设计仓。外部 ontology-enterprise 的实际 schema、命令参数、迁移接口和测试状态本次未验证。

## 3. 复用现有模块，不建立平行真源

- M1 建模编辑器：先用 YAML/JSON 源文件和只读图预览，后补可视化编辑。
- M2 候选抽取：保持后置；AI 仅 propose，证据不可省略。
- M3 变更治理：复用审批、版本冻结、append-only 审计；补充发布清单和激活协议。
- M4 影响分析：先人工维护依赖清单，再自动扫描。
- Ontology Registry：已批准 schema 包的权威索引；初期可为 Git 中受审查的清单，不要求独立服务。
- Catalogue：只读展示投影，不是新的写入真源，不存生产实例和凭据。
- Connector Registry、Metric Registry、Workflow Registry 保持各自职责，只引用本体的精确版本。

类型定义的权威来源为已批准 schema 包；Context 的权威内容是带时间与来源的业务状态，不能用类型目录替代实例事实。权限定义引用现有 policy，不由图编辑器签发授权。

## 4. 最小 Canonical Ontology 契约（设计）

规范化本体模型 Canonical Ontology Model 仅统一语义结构，不建立第二份业务对象存储。YAML/JSON 是序列化方式，不意味着已有可运行解析器。

```yaml
ontology_id: factory.manufacturing
ontology_version: "1.0.0"
profile: runtime-v1
namespace: "urn:example:factory:manufacturing:"
owner: manufacturing-it
status: draft
object_types:
  ProductionOrder:
    id_property: canonical_id
    properties:
      canonical_id: {type: string, required: true}
      source_record_id: {type: string, required: true}
      planned_quantity: {type: decimal, unit: piece, required: true}
      completed_quantity: {type: decimal, unit: piece, required: false}
      business_status: {type: string, required: true}
    states: [released, in_progress, completed]
link_types: []
metric_refs: ["mes.production.completion_rate@1.0.0"]
policy_refs: ["production-read@1.0.0"]
```

该片段是契约示例，不是 ontology-enterprise 可直接导入的文件。Action、Metric、Policy 仅保存版本引用，其公式、执行代码和授权策略仍在各自注册表中。

规则：

1. ID 在命名空间内唯一；跨厂/跨源对象键由 tenant_id、source_system、object_type、source_record_id 的无歧义编码或注册映射产生，不能仅拼单号。不确定同一性时隔离候选，禁止自动跨系统合并。
2. 属性需定义类型、required、nullable、单位；required 不等于 non-null。缺字段、显式 null、数值 0 分开处理。
3. decimal 不经二进制浮点静默转换；单位不一致须显式转换并记录规则版本。时间使用带时区值，工厂日历和跨日班次由指标口径定义。
4. Link 定义稳定 ID、source_type、target_type、方向和两端 min/max；多源部分快照的关系缺失不直接推断现实世界违反基数。
5. subclass_of 为类型继承，不是业务 Link；MVP 限单继承，拒绝循环和未解析父类型，属性收窄需显式兼容性审查。
6. 业务状态 business_status 与 Context 的 observed/inferred/confirmed/retracted 是两个维度，不得混用。
7. schema 元数据声明不构成访问许可；运行时仍执行租户、工厂、对象和字段作用域检查。

## 5. Connector → Context 语义绑定

Tool Contract 在既有字段之外增加以下设计字段：

```yaml
ontology_binding:
  ontology_ref: "factory.manufacturing@1.0.0"
  profile: runtime-v1
  mapping_ref: "mes.production-order@1.0.0"
  output_type: ProductionOrder
  metric_refs: ["mes.production.completion_rate@1.0.0"]
```

mapping 包至少定义 source_schema_version、输入路径、目标属性、转换规则、枚举映射、单位、时区、对象身份和错误策略。转换仅调用白名单确定性函数；禁止 eval、任意表达式、动态 SQL 和任意 URL。

执行顺序：授权过滤 → 固定版本与哈希校验 → 源结构校验 → Raw → Canonical 标准化 → Ontology 类型/单位/状态/关系校验 → Context 物化 → 版本化指标计算。Adapter 负责协议标准化，语义映射和规则归 Ontology/Mapping，不让 Adapter 自行创造类型或指标。

每个 Context 结果保存 ontology_ref、mapping_ref、tool_version、policy_version、source_schema_version、source_ref、trace_id、observed_at、received_at 和有效时间；保留可信状态，不因校验通过就自动升为 confirmed。

失败分类至少包括 SCHEMA_MISMATCH、MAPPING_MISSING、TYPE_INVALID、IDENTITY_AMBIGUOUS、VERSION_UNAVAILABLE、POLICY_DENIED。错误不能伪装成成功空列表。默认整次拒绝物化；如显式允许部分结果，返回 rejected_count、隔离引用和 partial 标志，指标默认拒绝使用部分输入。未知业务枚举保留源值并隔离，不映射为成功状态。

## 6. 版本、发布与恢复

生命周期：draft → validated → approved → published → active → deprecated/revoked。审批、发布和运行时激活是不同事件。

发布包记录精确版本、schema hash、mapping/metric/tool/workflow 依赖、审批引用、样本回放报告和兼容性结论。相同版本不同内容必须拒绝；MVP 以哈希和受保护仓库为完整性基础，不强制建设签名基础设施。

- 新增可选字段为兼容候选，仍需消费者验证；必填字段新增、字段删除/改型、单位变化、ID 规则变化、枚举收紧、基数或权限收紧按破坏性变更处理。枚举扩展也可能破坏封闭消费者。
- 新任务开始时固定依赖清单，任务内禁止解析 latest。普通升级不强制切换运行中任务；安全撤销后执行下一步之前必须阻断并转人工处置。
- 激活用原子清单切换，保留旧包；禁止各组件分别追踪 latest 导致混合版本。
- 存量 Context 不原地改写历史事实；新版本按显式迁移计划产生新投影，保留旧版本和来源。双版本窗口不是自动迁移策略，需负责人定义期限和对象范围。
- 回滚只恢复可兼容的配置和投影，不自动撤销已发生业务写入；外部副作用仍按既有 Action/Saga/回查处理。
- schema 发布不自动授予动作权限；只暂停受影响的不兼容 Workflow，而不是全量失效。

## 7. Profile 与导入安全

P0 只要求 runtime-v1 内部模型。外部交换 profile 在有明确消费者时再增加，不能先承诺 Fabric IQ 兼容或任意 RDF 的无损往返。

导入输出必须报告 unsupported_constructs、dropped_fields、warnings 和 profile_version。遇到无法表达的 Action/Policy/Metric 语义默认拒绝有损发布，或显式标记仅展示用途，禁止当作可执行包激活。UI 布局、颜色等展示元数据与业务 schema 分开，重排坐标不触发业务 schema 迁移。

RDF/XML 导入必须禁用外部实体与网络解析，限制大小/深度/节点数，拒绝任意远程取数；Markdown/标签按不可信输入净化。JSON-LD 若后续支持，远程 context 只能使用本地固定白名单。嵌入图只显示按授权过滤的 schema，不默认展示真实工单、库位或业务实例。

RDF/OWL 的开放世界语义不能直接等同本项目运行时闭世界字段校验；SHACL 导出也不代表已有 SHACL 验证器或 OWL 推理器。

## 8. 优先级、验证与验收

| 阶段 | 范围 | 验证方式 |
|---|---|---|
| P0 | 一个已确认试点的最小 schema、版本依赖、映射、人工影响清单 | 正常与负例样本回放；版本固定与来源字段检查 |
| P1 | M1 只读图预览、Catalogue 投影、文本/图 Diff | 同一包重现同一语义图；权限过滤；UI 不改变模型 |
| P2 | M4 自动影响分析、可视化编辑、需要时外部 profile | 精确版本源码构建；支持子集往返与丢失报告 |
| 后置 | 完整课程、Quest、AI Builder、嵌入实例图 | 单独立项，不能阻塞单系统只读闭环 |

P0 正反用例至少覆盖：正常映射、缺必填、null/0 区分、未知枚举、单位不一致、重复源 ID、跨租户同 ID、悬空关系、继承环、部分分页/快照、源 schema 漂移、版本缺失、同版本哈希不一致、安全撤销、任务中途升级和回滚后历史查询。安全负例必须全通过，其余结果报告通过数/总数；不得把这些设计用例写成已通过测试。

设备售后仍是既有优先候选；MES 是有条件的数据接入试点。未确认接口、账号和口径前，不把三项 MES 查询写成所有 MVP 的强制前置。可用脱敏 fixture 先验证契约，但必须标为离线验证，不能声称真实对接成功。

## 9. 实施前待确认

外部实现仓位置与版本；实际 schema/CLI/API；首个试点和业务负责人；样本/数据字典；ID 对齐与指标单位；网络和只读权限；是否确有 Fabric 消费者；是否需要独立前端。当前交付只完成文档设计，不创建新服务、不安装依赖、不访问生产系统、不自动提交推送。
