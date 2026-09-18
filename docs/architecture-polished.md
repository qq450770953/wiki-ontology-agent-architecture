# Wiki + Ontology 企业知识行动架构设计

> 版本：v1.8（2026-09-18，业务视角润色稿——章节结构、架构事实与 v1.8 技术稿一致，仅调整叙述方式）
> 状态：架构设计稿 + 参考实现映射（实现见 [ontology-enterprise](https://github.com/qq450770953/ontology-enterprise)）
> 读者对象：企业知识库 / AI Agent 平台的业务负责人、技术负责人、架构师、数据治理负责人
>
> 版本沿革：v1.6 吸收乌圆AI《自研 Ontology Engine 最小规格》4 项工程护栏（审计 6 字段 append-only、Saga 补偿、user edits win、治理优先排序）；v1.7 经架构评审补齐 Context 可信度闸门、Workflow 发布护栏、Saga 恢复协议、事件时间语义与 SLO 要求；v1.8 对照《智能体赋能汽车研发设计白皮书——从工具辅助到智能原生》（2026，中国汽研牵头、一汽/长安/理想等 15 家企业联合发布）吸收 2 项高优先级运行时安全机制（运行时动态降级、适用域声明）与 3 项增强点（技能资产治理、场景四维评估矩阵 + 数据治理先行、A/B 对照与"智能量"计量展望）。各机制详情见正文对应章节。

## 目录

1. [背景与问题](#1-背景与问题)
2. [设计目标与原则](#2-设计目标与原则)
3. [总体架构](#3-总体架构)
3.1. [Workflow 编排层](#31-workflow-编排层)
3.2. [上下文层（Context）](#32-上下文层context)
4. [核心流程：双路径 + 双闭环](#4-核心流程双路径--双闭环)
5. [组件职责](#5-组件职责)
6. [数据模型](#6-数据模型)
7. [Token 成本分析](#7-token-成本分析)
8. [知识复利机制](#8-知识复利机制)
9. [安全与治理](#9-安全与治理)
10. [风险与边界](#10-风险与边界)
11. [落地路线图](#11-落地路线图)
11.1. [参考实现映射](#111-参考实现映射)
12. [参考](#12-参考)

---

## 1. 背景与问题

很多企业已经把内部文档接入大模型，做了 RAG。员工问制度、查产品资料，系统基本能答上来，采购这套东西的预算也花得值。可一旦问题从"查资料"变成"办事情"，情况就不一样了。比如有人问：

> "帮我看看华东区上个月毛利为什么掉了，顺便找出影响最大的三个客户。"

系统往往就卡在这里。它能搜到《经营指标口径说明》，也知道公司有订单查询、客户分析、财务报表这些工具，但接下来每一步都是坎："华东区"对应哪些组织编码？"上个月"按自然月算还是按财务月算？"毛利"是毛利额、毛利率，还是某个版本的财务口径？该调哪个工具、参数怎么填？当前这个人到底有没有权限看这批数据？

问题出在哪？**"检索到相关内容"和"得到可执行的正确语义"是两件不同的事。** 传统 RAG 能找到相关片段，但实体消歧、口径解析、工具选择、参数绑定这些"把话说成动作"的环节，它天生管不了。

对业务方来说，这意味着一个直接的账：企业花了大模型的钱，员工得到的是一份份"看起来相关"的资料，而不是能落地的答案。知识没有变成生产力，每次提问都在重新付费、重新出错。这份架构要解决的，就是从"能搜"到"能做"的这段距离。

## 2. 设计目标与原则

整套设计围绕四个目标展开，它们彼此支撑，缺一不可。

第一是**省钱省得可持续**。高频问题不值得每次都让大模型从头推理。已经固化下来的问题范式，直接调用 Wiki 里注册好的 LangGraph Workflow——确定性节点不调用 LLM，延迟用 P50/P95 SLO 实测说话，结果可复现、可审计；LLM 只处理真正的新问题。配合"先查记忆、命中即复用"的入口设计，系统用得越久，单次成本越低。

第二是**答案建立在企业的真实状态之上**。Agent 需要的不是更多数据，而是正确、相关、实时、结构化、可验证的上下文。执行结果先按 Ontology 物化为 Context 对象，再交给 LLM 和下游决策——这是"上下文先于更多数据"的原则。

第三是**知识要能复利，认知要能同步**。未命中的新问题先走六步验证管线，把事做对；确认无误后，再把这次执行轨迹编译成可复用的 Workflow，经人工审批写入 Wiki——下次同类问题就不用重来了，这是知识闭环。与此同时，执行产生的新状态回流更新 Context，让 Agent 下一轮决策基于企业"现在"的样子而不是上次的快照，这是认知闭环。

第四是**一切可控、可查、可收回**。口径、权限、版本、来源全程留痕；Workflow 固化必须过人工审批；过期规则必须失效。错误最可怕的形式不是"答案不对"，而是"看不见错误"，所以护栏先于自主放大：评估先行、授权分级、监控兜底，临时放行绝不静默升级为常设权限，经验再多也不跳过里程碑检查。

最后一条是务实的落地态度：**不从全公司知识图谱开始**。从一个高频、数据清晰、结果可验证的业务场景起步，跑通了再扩。

## 3. 总体架构

方案分八层，从上到下是用户请求的必经之路，治理层横切所有层：

```
┌─────────────────────────────────────────────────────────────┐
│ 入口路由层                                                    │
│  Wiki 层查询（索引匹配）→ 命中判定 → 双路径分发                            │
├─────────────────────────────────────────────────────────────┤
│ 知识层（LLM Wiki）                                            │
│  Raw Sources（原始资料，不可篡改）                                  │
│  Wiki（实体页 / 概念页 / 模板库 / 映射表 / 固化流程注册表）                   │
│  固化流程注册表 = 已审批 Workflow 的目录（指向 LangGraph 定义）             │
│  Schema（目录、命名、写入查询规则，Wiki 治理规范）                          │
├─────────────────────────────────────────────────────────────┤
│ 语义层（Ontology）                                            │
│  实体（唯一ID / 别名 / 关系 / 口径版本 / 生效时间 / 权限约束）                 │
│  图检索（多跳） · 记忆分类（policy 引用 / authz 限 scope）               │
│  —— 定义世界结构                                               │
├─────────────────────────────────────────────────────────────┤
│ 上下文层（Context）                                            │
│  上下文物化 → ContextPack 组装（持久化→筛选→压缩→隔离）                    │
│  状态/事件/历史/因果 · 回流更新 · TTL 保留 · 反投毒审查                     │
│  —— 每次按预算组装后喂给 LLM（防 Context Rot / 中段丢失）                 │
├─────────────────────────────────────────────────────────────┤
│ 执行层                                                      │
│  命中路径：调用已固化 Workflow（LangGraph 图直接执行）                    │
│  未命中路径：六步自主管线（映射 → 消歧 → 绑定 → 执行 → 物化 → 汇总）               │
├─────────────────────────────────────────────────────────────┤
│ Workflow 编排层（LangGraph）                                  │
│  固化 = 确认后按执行轨迹构图（编译为可复用 Workflow 定义）                     │
│  命中 = 加载注册表指向的已编译图直接执行 · 状态/重试/审计                        │
├─────────────────────────────────────────────────────────────┤
│ 接入层                                                      │
│  MCP / CLI / API / SQL —— 查询或操作真实业务系统（观察世界）              │
├─────────────────────────────────────────────────────────────┤
│ 治理层（横切）                                                  │
│  分级授权 · 固化人工审批 · Context 权限/审计 · 版本失效 · Lint             │
│  四层评估（Outcome/Trajectory/Golden/CI回归）· 运行监控 · 反投毒        │
│  Workflow 固化 = 生成定义 → 人工审批 → 过审后更新 Wiki 连接               │
└─────────────────────────────────────────────────────────────┘
```

理解这套架构，有几个关键认知值得展开说说。

Wiki 不是辅助组件，它是整个系统的记忆核心。入口靠它过滤请求，出口靠它沉淀成果。要注意的是，Wiki 存的是"连接"而不是"实现"——固化流程注册表只保存已审批 Workflow 的目录和指针，命中时把图加载到 LangGraph 直接执行，不重新编排。

Ontology 和 Context 的分工，可以用一句话记住：**Ontology 定义世界，Context 重建世界**。Ontology 描述"世界由什么构成、遵循什么规则"，这部分结构相对稳定；Context 描述"世界现在处于什么状态"，实时且不断变化。只有 Ontology 没有 Context，系统就只是一套高级知识建模，Agent 感知不到"正在发生什么"。Context 既是行动的结果，也是下一次决策的输入——执行产生的新状态回流进 Context，Agent 据此推理下一步，这与知识复用闭环相互独立又互相增强。

业务明细不进 Wiki，但可以物化进 Context。Wiki 保存语义抽象与使用规则，实时状态与历史演化由 Context 维护，明细数据仍由各业务系统持有。也正因为如此，共享语义不等于共享数据权限：Agent 知道"存在某项数据"，不代表有权读取具体数值。

授权这件事有一条铁律——不得静默常设。一次带时间和额度限定的临时放行，如果被会话沉淀压缩成"用户允许"，就等于绕过了审批。所以记忆按类型区分，策略只引用不复制，授权限作用域并默认过期（见 §6.9）。

喂给 LLM 的上下文也不是越多越好，而是按预算组装的。Context 解决"世界状态从哪来"，ContextPack 组装解决"每次喂什么、喂多少"——先持久化，再筛选，再压缩，再隔离。

### 3.1 Workflow 编排层

编排层以 LangGraph 为载体，把"命中 / 未命中"两条路径上的步骤统一建模为可定义、可观测、可复用的有状态图。确定性操作（模板执行、参数校验、权限检查）和 LLM 操作（消歧、汇总解释）都注册为图节点，每个节点声明输入输出 Schema、超时与重试策略。

这里有一条最重要的顺序原则：**先执行、后构图**。未命中时先直接执行六步自主管线，全程受治理与审计约束、保留执行轨迹，确认无误之后再按轨迹构图——而不是在结果未知前就先画好图。命中的固化流程则是注册表指向的已编译图，加载即执行。

图的实例状态持久化（running / waiting_input / success / failed / timeout），步骤级日志、耗时与 token 消耗全程可审计，支持断点续跑与人工介入。护栏内建在图里：重试策略、超时熔断、降级（LLM 节点失败落到确定性兜底），所有节点统一受 RBAC 与审计约束。

自主度是按图声明的（auto / supervised / gated，详见 §4.4）。低复杂度链路可以自主运行，高复杂度或高危变更保留里程碑 `human_approval` 检查点；运行期以实时异常告警加周期检查点加事后人工审查为主，而不是每一步都等批准——逐级审批会疲劳，疲劳的审批等于没有审批。长周期任务还支持 checkpoint → clear → reload 三步上下文重置，同一工具连续 N 次无进展就触发重置或转人工，不让状态污染下的 Agent 硬撑（参考 §3.2）。

固化机制是编排层与 Wiki 的接口：六步管线执行成功并经正向反馈确认后，把本次执行轨迹编译为可复用的 Workflow 定义，提交人工审批；审批通过才允许写入 Wiki 的固化流程注册表并建立连接。**审批未过不写 Wiki，绝不绕过。**

一句话概括：执行层定义"能做什么"，六步管线负责"把事做对"，编排层负责"把验证过的做法固化成图"，人工审批决定"什么能成为下一次的固化流程"。

### 3.2 上下文层（Context）

在语义层与执行层之间引入上下文层，解决的是"只有 Ontology 不知道世界现在是什么状态"的问题。它建立在 Ontology 对象模型之上，持续重建企业当前的世界状态。

核心动作是**上下文物化**：执行层从各系统取回的异构数据（结构化、半结构化、实时流、业务事件、指标变化），先按 Ontology 对象模型映射为 Context Object——对象、状态、事件、时间、来源五要素齐全——再交给 LLM 汇总与下游决策。Agent 拿到的是结构化、可验证的上下文，不是一坨原始 JSON。

每个 Context Object 记录当前状态（state）、事件日志（event_log）、历史快照（history）、因果链（causality）与影响传播（dependency），这样"什么时候开始变的、怎么演化的、为什么会变、影响什么"这类问题才有据可答。

写入有闸门。Agent 行动或查询确认之后，新数据、新事件、新状态先写进候选区，通过来源、权限和业务确认闸门后才提升为正式 Context。Agent 决策前读取的就是这份"当前世界状态"，不必每次重新查询所有系统。

喂给 LLM 的部分由 **ContextPack 组装**完成，按四原语来：一是持久化，只从可信物化存储取数；二是筛选，按当前意图与预算裁剪相关对象与工具，工具按白名单动态注入，而不是一百个工具全塞进 prompt；三是压缩，历史快照分级压缩、摘要化；四是隔离，租户、会话、任务互不串扰。ContextPack 携带 `system / tools / memory / evidence` 四段并内置 `trace_id`，专门对策上下文腐烂（Context Rot）和中段丢失（Lost in the Middle）。

记忆有保留期，也有免疫系统。Context 和 Lesson 带 TTL（按对象类型分级：高频状态短、稳定事实长）；写入前做反注入审查——只接受 schema 白名单字段，拒绝"忽略以上规则…"这类指令性文本，网页、邮件、工单原文等高危外部来源与内部结构化状态分区隔离存储。记忆落库前先判定类型：偏好（preference）与事实（fact）可沉淀；策略（policy）只保留引用、不复制全文，保证策略变更新旧一致；授权（authorization）必须绑定作用域（对象、动作、额度）加生效窗口，默认过期（详见 §6.9）。

状态的可信度是分级的。Context Object 强制记录 `tenant_id`、`subject`、`data_classification`、`policy_version`、`observed_at`、`received_at`、`effective_from`、`effective_to`、`computed_at` 九个治理字段；状态区分 `observed`、`inferred`、`confirmed`、`retracted` 四态；迟到事件按事件时间重放，冲突时保留各源值与优先级。

还是那句话：**Ontology 定义世界，Data 观察世界，Context 重建世界，Agent 理解世界并改变世界；改变之后的新状态又回流进 Context，形成企业认知闭环。**

## 4. 核心流程：双路径 + 双闭环

完整链路如下图所示：

![完整链路架构图](images/pipeline.svg)

### 4.1 入口：Wiki 层查询（过滤）+ Context 状态读取

用户提问后，系统先用确定性索引（关键词、向量、实体索引）查 Wiki 层，而不是直接调用 LLM。查的东西有两类：一类决定"怎么执行"——关键词映射表、SQL 模板库、固化流程注册表（问题范式到已审批 Workflow 的映射）；另一类决定"基于什么现状执行"——Context 里相关对象的当前状态、事件与历史。

命中判定分三级：

| 判定 | 条件 | 动作 | Token |
|---|---|---|---|
| 完全命中 | 问题范式与实体集合均匹配 | 直接调用已固化 Workflow | 视图中节点而定，确定性图 ≈ 0 |
| 部分命中 | 范式匹配、参数不同 | 调用 Workflow 并替换参数 | 同上 |
| 未命中 | 注册表无匹配 Workflow | 走右侧六步自主执行管线 | 数千级 |

### 4.2 命中路径（左侧）：直接调用已固化 Workflow

命中后从注册表取出记录，加载其指向的 LangGraph Workflow 定义直接执行——不再重新编排，不重复消耗 LLM 的规划能力；执行基于入口读取的 Context 当前状态。纯确定性图（模板 SQL 加参数）全程不调用 LLM，端到端延迟和含 LLM 节点的 token 消耗以 P50/P95 SLO 实测为准。结果可复现，全程可审计。固化条目越多，命中率越高，系统平均成本持续下降——这是这套架构经济效益的来源。

### 4.3 未命中路径（右侧）：六步自主执行管线

没命中怎么办？直接干活。此时不预构图，先把事做对：全程受 RBAC、参数白名单与步骤级审计约束，并保留完整执行轨迹（节点序列、参数、决策、耗时），供确认后的构图固化使用。这是 LLM 真正"干活"、消耗 token 的地方，共六步。

第一步，查关键词映射表，把自然语言拆解为业务类型（区域、时间、指标、维度），得到候选业务对象。第二步，Ontology 实体消歧，把候选词解析为标准实体 ID、指标口径版本和期间（结合财务日历与关账状态），多口径定不下来就问用户，不猜。第三步，工具映射与参数绑定，依据工具目录（能力、参数 Schema、权限、风险）选工具，按模板拼装 SQL 或绑定参数，执行类型、枚举、时间范围、权限校验。第四步，各系统执行——SQL、MCP、CLI 到数据库、数仓、CRM 等系统查真实数据。写入类动作的外部副作用走 Saga 异步补偿：状态机 PENDING → COMMITTED / ROLLBACK，失败按步骤逆序补偿，不用 2PC（跨库 2PC 死锁多、可用性差）；补偿逻辑与幂等键在动作注册时声明（§11.1 `action register` 已有 idempotency 字段）。Saga 必须持久化编排状态和 outbox 事件，外部调用超时、补偿失败或状态不明时进入重试/对账队列，超过上限转 `MANUAL_REVIEW`，不得静默标记成功。第五步，上下文物化——把异构系统返回按 Ontology 对象模型映射为 Context Object 写入 Context 层，随后组装为 ContextPack（按意图与预算筛选、动态注入白名单工具、压缩历史快照、隔离租户边界），只把组装后的包交给下游（见 §3.2）。物化写入采用字段级覆盖：保留 `source_value` 与 `effective_value`，记录覆盖人、原因和过期时间；人工或 Action 修改过的属性不被源系统再同步覆盖（user edits always win），源系统新值进待处理区，不被静默丢弃。第六步，LLM 汇总解释——读取组装后的 ContextPack，输出排序、对比、归因、指标口径、数据状态与用过的工具，而不是转述 JSON。

执行原则一句话：**LLM 只填参数、不自由造 SQL**。模板 SQL 加参数白名单校验（参数只能来自 Ontology 实体），从源头规避注入、越权与口径错配。

### 4.4 确认信号与分级自主

执行完成后，只有拿到确认信号才允许固化。确认信号必须是客观的：用户采纳、点赞或多次同类问题未纠错；结果与人工核对一致；评测用例通过。注意确认信号只允许触发候选状态写入——只有业务确认或来源事实校验通过，结论、归因与新状态才能提升为正式 Context。用户点个赞只能算反馈证据，不能单独升级事实状态。

审批机制经历过一次重要升级：从"什么都等人工批"升级为"分级自主 + 运行期监控"。逐级审批会疲劳化、形式化，成熟经验用户的做法是提高自动批准率，但在里程碑检查点中断（参考 §9.7.4 / §9.9）：

| 复杂度档 | 判定依据 | 自主度 | 监控与介入 |
|---|---|---|---|
| 简单 | 只读查询、确定性模板、低风险 | 全自主执行 | 事后抽查 + 结果对比 |
| 中 | 含 LLM 汇总 / 归因 / 多步编排 | 自主执行，轨迹留痕 | 实时异常告警 + 抽样人工审查 |
| 高 | 跨系统写入 / 参数越权风险 / 新数据源 | 执行即停（里程碑 `human_approval`） | 10–15 分钟检查点 |
| 极高 | 付款 / 发消息 / 高危变更 / 首次固化 | 强制人工审批 | 双人复核 + 全量审计 |

配套三条治理要点：鼓励 Agent 表达不确定性，口径或权限不清晰时主动澄清，而不是带猜测继续——这是检查点质量的前提；运行期监控优先于预先规范，实时异常告警（工具失败率、token 突增、越权尝试）比把每步都设成人工确认更能防事故；事后审查闭环，里程碑检查点与抽查发现的坏轨迹回流 Golden Dataset（§4.7）与 Lesson 负向知识。

v1.8 补上了运行时动态降级这条回路：上表只是部署时的基线，不是终身配置。参考汽车研发白皮书的做法——智能体连续输出低置信度结果或出现异常模式时，系统自动切回人工运行模式。具体规则：同一 Workflow 或场景连续 N 次低置信度结果、四层评估指标越回退阈值（§4.7）、异常告警（工具失败率 / 越权尝试）超限，自动把自主度收紧一档（auto→supervised→gated）并告警值守；恢复要等连续 M 次通过检查点加 Golden 回归通过，才逐档放宽，降级与恢复事件全部留痕审计。命中路径也有对应的超域降级：固化流程条目带适用域声明（§4.5 / §6.5），执行前超域检查不通过，本次执行自动降级 supervised。评估由此不只是告警，还驱动自主度调整，形成"评估 → 调节 → 再评估"的闭环。

### 4.5 固化：生成 Workflow 定义 → 人工审批 → 更新 Wiki 连接

确认之后进入固化链路，也就是知识闭环。核心动作是把本次执行轨迹构图为可复用的 Workflow，而不是把经验文字直接写回 Wiki。

流程分三步。LLM 依据执行轨迹起草 Workflow 定义（节点、边、参数绑定规则、兜底策略），提交审批队列；治理层做风险分级审核——新 Workflow 的固化属于高风险变更，强制人工审批，审批意见要么通过、要么退回修改；审批通过后把新 Workflow 注册进 Wiki 的固化流程注册表，记录问题范式、Workflow ID、版本、生效时间、负责人和适用域 `applicable_domain`，旧版本标记"已替代"而不是删除。审批未通过，就不产生任何 Wiki 连接。

适用域声明是 v1.8 新增的关键字段：这条流程可以自动执行的对象类型、状态范围与参数边界。命中执行前做超域检查，超出适用域自动降级 supervised（见 §4.4）。

发布前还有一道工程闸门：冻结 Workflow、工具、Schema 和策略版本并保存内容摘要；通过 DSL/schema 校验和权限、危险工具、循环、异常分支的静态检查后，先灰度或影子运行，再扩大流量。注册表必须支持撤销、停用、回滚和运行中实例处置，不能只靠一个 `status` 字段切换。

不同固化内容的审核级别不一样：

| 固化内容 | 写入位置 | 审核级别 |
|---|---|---|
| 新别名（"华东大区"→SalesRegion） | 映射表 | 低，可自动 |
| 新问题范式（"对比X月与Y月毛利"） | SQL 模板库 | 中，抽查 |
| 新工具 / 新数据源 | 工具目录 | 中 |
| **新 Workflow（动态编排 → 编译定义）** | **固化流程注册表** | **高，强制人工审批** |
| 指标口径确认 | Ontology 指标版本 | **高，强制人工** |
| 失败案例（模板不可用、参数越权） | 负向知识页 | 低，建议保留 |

### 4.6 双闭环回环

到这里可以看清这套架构的全貌了：两个相互独立又互相增强的闭环。

知识闭环管"复用降本"。审批通过并更新 Wiki 连接后，下次同类问题从右侧未命中路径"降级"到左侧命中路径——入口查注册表命中，直接调用已固化的 Workflow，不再动态编排，token 从数千级降到趋近零。

认知闭环管"状态同步"。执行确认后，结论、归因与新状态回流更新 Context，后续 Agent 决策基于世界当前状态而非执行时刻的快照。比如确认"华东区毛利下滑主因是华东客户 X 订单流失"后，这个归因和状态写入 Context，之后再有人问"为什么毛利下滑"，直接基于 Context 回答，不用重新全量查询。

**这套架构最有价值的不是某一步，而是"执行 → 物化 → 确认 → 构图 → 审批 → 注册 → 命中调用（知识闭环）+ 状态回流 → 基于现状决策（认知闭环）"两条回路本身。**

### 4.7 四层持续评估

评估不能是"上线时测一次"的仪式，得是持续机制（参考 §9.7.4 / §7.3——最贵的错误是只评答案、不评过程）：

| 层 | 评估对象 | 手段 | 落地位置 |
|---|---|---|---|
| L1 Outcome | 最终答案对不对 | 黄金集比对 / 人工核对 | 入口路由层结果校验 |
| L2 Trajectory | 中间步骤是否绕路 / 循环 / 越权 / 走错工具 | **轨迹三问**：够不够快（步数）、够不够稳（重试/死循环）、够不够安全（越权/越界） | 执行层 + Workflow 编排层（§7） |
| L3 Golden Dataset | 回归样本库持续扩充 | 生产 trace 中人工纠正的坏例子**回流黄金集**（含负向样例） | 评测集（§11 步骤 7） |
| L4 CI/CD 回归 | 换模型 / 换 prompt / 换工具 / 改护栏后行为不退化 | 每次变更自动跑全量回归，失败即阻断发布 | 治理层门禁 |

发布门槛至少包括：安全越权率为 0；Outcome、工具误选率、超时率、重复执行率和 Saga 补偿成功率相对基线不得超过预设回退阈值；任一安全指标或审计完整性检查失败即阻断发布。具体阈值在试点场景验收时固化。

方法论上，v1.8 补充了对"智能体辅助 vs 传统人工模式"的 A/B 对照测试：量化对比吞吐、返工率、正确率，作为自主度放宽（§4.4）与试点推广决策（mvp-plan 四维矩阵）的依据。评估结果同时驱动运行时自主度收紧或放宽——评估不只告警，还要闭环到 §4.4。

有一条关键闭环值得单独强调：**线上发现的"答案对但轨迹绕 / 轨迹违规"必须回流 L3 黄金集**，否则同类坏轨迹会反复出现而无人察觉。

## 5. 组件职责

各组件的分工与边界如下表。划清"不该干什么"和"该干什么"同样重要——职责蔓延是治理失控的开端。

| 组件 | 主要职责 | 不应承担的职责 |
|---|---|---|
| RAG | 检索大规模原始资料和长尾证据 | 单独决定业务实体和工具参数语义 |
| LLM Wiki | 沉淀稳定定义、规则、关系、经验、来源；**维护固化流程注册表（已审批 Workflow 的目录）**；入口过滤、出口注册 | 保存实时交易明细或替代业务系统 |
| Ontology | 统一实体、别名、关系、指标版本、约束和工具语义（定义世界结构） | 代替原始证据和数据权限系统 |
| **上下文层（Context）** | **上下文物化、ContextPack 组装（筛选/压缩/隔离）、状态/事件/历史/因果建模、行动后回流、TTL 与反投毒** | 替代 Wiki 沉淀稳定定义；替代业务系统保存明细 |
| 映射表 / 模板库 | 关键词→实体、问题→模板的确定性转换 | 覆盖开放、模糊表达（交 LLM 兜底） |
| Workflow 编排层（LangGraph） | 节点注册、DAG 编排、状态管理、重试/超时/降级、步骤级审计、**动态图编译** | 自行解释模糊业务口径（交 LLM / Ontology） |
| MCP / CLI / API | 查询或操作真实系统（观察世界） | 自行解释模糊自然语言业务口径 |
| Agent / ReAct | 规划、选择工具、绑定参数、解析观察并组织结果 | 绕过权限、直接发布高风险变更 |
| 反馈审核层 | 确认信号判定、分级授权、**动态自主度调节（运行时降级）**、**适用域超域检查**、**Workflow 固化人工审批**、四层评估、运行监控、Context 更新审计、版本失效 | 无脑全量固化；把临时放行静默转常设 |

一句话总结：RAG 负责找证据，LLM Wiki 负责沉淀认识并以固化流程注册表充当记忆入口，Ontology 负责统一语言，Context 负责重建世界当前状态，Workflow 编排层负责把各环节组织成可观测、可复用的图，人工审批决定哪些图能进入注册表，工具负责访问事实。

## 6. 数据模型

### 6.1 关键词映射表

```
keyword: 华东大区
business_type: SalesRegion
aliases: [华东, East China, 东区]
target: region.east_china
```

### 6.2 Ontology 实体

```yaml
id: region.east_china
type: SalesRegion
name: 华东区
aliases: [华东, East China, 东区]
contains: [org.shanghai, org.jiangsu, org.zhejiang]
valid_from: 2026-01-01
source: 2026版销售组织架构
status: verified
```

### 6.3 指标实体（含口径版本）

```yaml
id: metric.gross_margin_rate.v3
type: BusinessMetric
name: 毛利率
formula: (不含税收入 - 可归属成本) / 不含税收入
time_basis: 财务月
dimensions: [region, customer, product]
data_source: finance_mart.profit_detail
replaces: metric.gross_margin_rate.v2
effective_from: 2026-04-01
```

### 6.4 SQL 模板

```yaml
template_id: tpl.profit_by_region_period
question_pattern: 查询{区域}在{期间}的{指标}
params:
  region_id: {entity: SalesRegion}
  start_period: {format: YYYY-MM}
  end_period: {format: YYYY-MM}
  metric_id: {entity: BusinessMetric, require_effective: true}
sql: |
  SELECT {metric.expression} FROM {metric.data_source}
  WHERE region_id = :region_id AND period BETWEEN :start_period AND :end_period
target_system: finance
required_permission: finance_read
```

### 6.5 Wiki 固化页面

每个 Wiki 页面至少包含唯一 ID、类型（实体/概念/流程/模板）、标题、正文、来源、版本、生效时间、状态（active / superseded）、负责人和关联页面。其中"流程"页就是固化流程注册页，额外保存 `workflow_id`（指向 LangGraph Workflow 定义）、审批记录和适用域 `applicable_domain`（对象类型 + 状态范围 + 参数边界，v1.8 新增），入口路由层凭它命中并加载对应图，命中执行前做超域检查（§4.4）。

注册页同时是资产页（v1.8 技能资产治理，参考汽车研发白皮书 §7.6.3）。一方面挂分类标签体系——领域标签、风险档位、触发范式标签，支撑检索、统计与场景聚类；另一方面做使用评价回流——执行次数、成功率、P50/P95 耗时、最近执行时间、用户反馈摘要。注册表由此从"静态目录"变成"活资产"，也为 §4.4 的动态自主度调节提供数据源。

### 6.6 Lesson 经验实体（经验沉淀）

借鉴 Agent Memory 的会话记忆模式，把"踩过的坑"沉淀为可检索、可关联、带血缘的资产，与 Wiki 知识页互补：Wiki 存知识（编译过的结论），Lesson 存经验（发生过的事 + 学到的）。

```yaml
id: lesson.port_conflict_20260818
type: Lesson
action: docker compose up          # 做了什么
context: local dev, port 3000      # 场景
outcome: negative                  # positive | negative
insight: check port before start   # 下次怎么做
area: dev                          # 领域标签
learned_from: task.dev_001         # 关联实体（可选）
status: active
source: session-2026-08-18
```

设计上有几个要点：四元组必填——做了什么（action）、什么场景（context）、结果如何（outcome，正/负枚举）、下次怎么做（insight)；复用与实体/指标同一套治理链（类型约束、RBAC、审计、血缘）；会话记忆协议——任务开始前 `lesson query --area <领域>` 加载相关教训，任务结束后沉淀新教训（失败复盘记 negative，验证有效记 positive）；负向知识参与复利，失败案例固化后，下次同类问题直接规避，不用重新试错。

### 6.7 Workflow 定义模型

编排层的核心数据模型把节点、边、护栏策略与审批/注册状态声明式建模。下面是一个"区域期间毛利查询"的示例：

```yaml
workflow_id: wf.profit_region_period
name: 区域期间毛利查询
runtime: langgraph                   # 编排载体：LangGraph
tool_version: mcp.finance.query@2026-09-05
schema_version: profit.v3
policy_version: finance.read.v2
dependency_digest: sha256:<workflow-content-digest>
status: approved                     # draft | pending_review | approved | rejected | superseded
registered_in_wiki: true             # true = 已过审并写入 Wiki 固化流程注册表，入口路由层可命中
release_state: canary                # draft | canary | active | paused | revoked
canary_percent: 10
rollback_target: wf.profit_region_period@1.1
nodes:
  - id: kw_map                       # 关键词映射
    type: deterministic              # deterministic | llm | tool | condition | human_approval
    module: keyword_map
  - id: disambiguate                 # Ontology 实体消歧
    type: llm
    module: entity_resolve
    retry: {max: 2, backoff: 1s}
    timeout: 30s
  - id: bind_params                  # 工具映射 + 参数绑定
    type: deterministic
    module: template_bind
  - id: exec_sql                     # 各系统执行
    type: tool
    module: mcp.finance.query
    timeout: 60s
  - id: summarize                    # LLM 汇总解释
    type: llm
    module: summarize
edges:
  - {from: kw_map, to: disambiguate}
  - {from: disambiguate, to: bind_params}
  - {from: bind_params, to: exec_sql}
  - {from: exec_sql, to: summarize}
on_failure: fallback_to_deterministic    # LLM 节点失败降级到确定性兜底
audit: step_level                        # 步骤级审计：节点序列 / 参数 / 耗时 / token / 决策日志
approval:
  required: true                         # 固化必审：动态编排 → 编译 → 人工审批 → 过审注册
  approved_by: 数据治理负责人
  approved_at: 2026-08-18
```

几个设计要点值得说明。节点类型是封闭枚举：确定性节点、LLM 节点、工具节点、条件分支节点（如"多口径无法确定 → 询问用户"）、流程内审批节点（高危操作闸口）。固化即注册——`status: approved` 且 `registered_in_wiki: true` 的 Workflow 才会被入口路由层命中，draft 和 pending_review 的图只能用于动态执行，永不直接进入命中路径。两种审批要分清：`human_approval` 节点是流程运行中的高危操作闸口，`approval` 块是固化治理闸口，决定"这张图能否成为下一次的固化流程"。护栏是节点级属性，超时、重试、降级在每个节点上声明，不做全局一刀切；自主度则是图级属性，Workflow 声明 `autonomy`（auto / supervised / gated）与 `complexity_tier`（简单/中/高/极高，见 §4.4），路由层据此决定运行期是"放行 + 监控"还是"里程碑等批"，`autonomy: gated` 的图必须含 `human_approval` 节点。另外，`summarize` 等 LLM 节点返回前附加 `trajectory_ok` 标记（工具选择是否合理、是否多次重试），供 §4.7 L2 Trajectory 采集。

### 6.8 Context Object 与因果链模型

上下文层的核心数据模型，把"企业当前世界状态"与"如何变化"声明式建模：

```yaml
context_id: ctx.asset.device_a.20260819T0845
tenant_id: tenant.acme
subject: user.u123
data_classification: internal
policy_version: asset.read.v2
object: asset.device_a                 # 关联 Ontology 对象（对象模型由 Ontology 定义）
state: abnormal                        # 当前状态（枚举，由类型 schema 约束）
confidence_state: observed              # observed | inferred | confirmed | retracted
state_since: 2026-08-19T08:15:00
events:
  - {ts: 08:15, type: temp_rise, value: 87C, source: iot.sensor}
  - {ts: 08:18, type: vibration_abnormal, source: iot.sensor}
  - {ts: 08:20, type: capacity_drop, value: -15%, source: mes}
history:                               # 历史快照，支撑时间维分析（"什么时候开始变的"）
  - {ts: 08:00, state: normal, value: 80C}
  - {ts: 08:15, state: warning, value: 87C}
causality: causal.delivery_risk_20260819   # 关联因果链（见下）
dependency: [order.o123, customer.c456]    # 影响传播：影响哪些业务对象
valid_at: 2026-08-19T08:45:00
provenance: {source: iot.gateway, query_id: exec_001}
observed_at: 2026-08-19T08:44:58
received_at: 2026-08-19T08:45:01
effective_from: 2026-08-19T08:15:00
effective_to: null
computed_at: 2026-08-19T08:45:02
```

归因不用 LLM 即兴发挥，而是结构化的因果链：

```yaml
causality_id: causal.delivery_risk_20260819
event: asset.device_a 状态异常          # 起因
affects: order.o123 交付延迟风险          # 影响
reason: 设备产能下降 → 订单生产进度停滞    # 传播路径（可多跳）
evidence: [ctx.asset.device_a, ctx.order.o123]  # 证据指向的 Context Object
status: confirmed                       # confirmed | hypothesis（确认后升级）
```

设计要点：Context 建立在 Ontology 之上，`object` 必须是 Ontology 实体，state 枚举由类型 schema 约束，物化即校验；物化本身是写入动作，六步管线的第 5 步把各系统返回映射为 Context Object，LLM 汇总与下游决策只读 Context，不再直面原始 JSON；因果链初始为 `hypothesis`，经 §4.4 正向反馈确认后升级为 `confirmed` 并回流 Context——归因由此变成可审计、可复用、可追溯的状态资产。与 Wiki / Lesson 的分工也在这里看清：Wiki 存稳定知识与固化流程，Lesson 存操作经验，Context 存当前状态与演化，三者互补，构成"知识 + 经验 + 状态"的完整记忆体系。

Context 状态有完整闸门：`candidate → validated → confirmed → active → superseded / retracted`。`inferred` 只可用于低风险、可回滚决策；`confirmed` 必须有业务主体、证据或人工审核记录；`retracted` 会阻止依赖该状态的 Workflow 命中，并触发关联 Context、Lesson 和 Workflow 的重新评估。候选区默认不进入正式决策 ContextPack。

### 6.9 记忆类型分类与授权防放大

治理叙事里最危险的口子叫授权放大（Authorization Creep）。场景是这样的：业务主管临时批了一句"这个月你可以自行批 15% 折扣"，这本是一次带时限的放行；但如果会话沉淀把它压缩成"用户允许 15% 折扣"，一次性的授权就悄悄变成了常设权限，而且绕过了审批。对策十二个字：**记忆先分类、策略引用不复制、授权限 scope 且默认过期**。

```yaml
# preference 用户偏好（可沉淀为记忆）
memory_id: mem.preference.report_format
type: preference
content: 汇报用中文、按区域分节、附环比
scope: report
source: session-2026-09-03

# fact 事实（可沉淀为记忆）
memory_id: mem.fact.region.east_china.closed
type: fact
content: 华东区 2026-08 已关账
valid_at: 2026-08-31

# policy 策略（只存引用，不复制全文——保证策略变更新旧一致）
memory_id: mem.policy.ref.discount_policy.v7
type: policy
ref: policy_system.discount_policy#v7
effective_from: 2026-07-01

# authorization 授权（必须绑定 scope + 生效窗口，默认过期）
memory_id: mem.authz.li.discount_15pct.202609
type: authorization
principal: user.li
action: approve_discount
scope: {max_rate: 15%, regions: [east_china], expires_at: 2026-09-30}
status: active
```

分类规则在写入和检索时强制执行。写入前先分类，分不清的宁可存为 `fact` 并挂来源，不猜成 `policy` 或 `authorization`。策略类只保留指向策略系统的引用，策略变更只需改一处，记忆里不会留下陈旧副本。授权类必须带 `expires_at`，不填默认短期（如 24h），续期是显式动作、不是静默延长。检索授权记忆时校验 principal、scope、时效是否覆盖当前请求——命中授权不等于豁免流程内 `human_approval`，那是另一道闸。还有一条容易忽视的：从"人工多次放行"归纳出的规律只能作为策略复审线索，提示治理负责人"近 N 次同类操作均被放行，是否调整策略"，绝不自动升级为权限主张；复审通过才更新策略系统。

## 7. Token 成本分析

这笔账业务方最关心，直接看数：

| 方案 | 每次查询 token 估算 | 说明 |
|---|---|---|
| 本架构命中路径（调用已固化 Workflow） | **≈ 0–1k** | 纯确定性图 0 token；含 LLM 节点（如汇总）时少量 |
| 本架构未命中路径（六步自主管线） | ≈ 1k–10k | 六步管线中消歧、绑定、汇总消耗 |
| 纯 RAG + ReAct 多轮 | ≈ 10k–50k+ | 检索上下文 + 多轮工具调用往返 |

以上是量级估算，具体取决于模型、上下文长度与问题复杂度。架构通过"入口确定性过滤 + 命中优先"把大多数高频查询压到极低 token，且随固化累积，命中率持续上升。

但只算 token 单价会自欺欺人。全成本口径（参考 §9.7.6）应该把 token 费、失败重试、人工兜底、运行监控、事故恢复一起算——否则容易出现"token 便宜了、人工更贵了"的假优化。每次未命中执行对照轨迹三问：够不够快（绕了几步、有无循环）、够不够稳（重试几次）、够不够安全（越权尝试、越界字段），供 §4.7 L2 Trajectory 采集，不只记 token 数。

顺带一个前瞻（v1.8）：随智能体规模化，成本口径可以从 token 延伸为按任务/场景计量的"智能量"——把每次固化命中节省的 token 折算为复利收益，作为 §4.7 A/B 对照与场景推广排序的量化指标（参考汽车研发白皮书 §13.3）。

## 8. 知识复利机制

所谓复利，就是每次成功执行都让下一次更便宜、更快、更准、更了解现状。这件事不是自动发生的，成立前提是四个护栏：确认信号必须是客观的正向反馈（采纳、核对、评测通过），防止错误固化；新 Workflow 固化（执行轨迹 → 构图定义）强制人工审批，指标口径、实体合并、权限这类高风险变更同样强制人工，低风险自动写回，审批通过才允许更新 Wiki 连接；固化条目带版本与生效时间，组织调整、口径变更、数据源下线时及时标记失效，别让旧知识长期误导；执行确认后新状态回流更新 Context，Context 同样受权限与审计约束，状态漂移由 Lint 定期校准。

负向知识同样参与复利："这条路走不通"固化成规则，下次直接规避。

### 8.1 会话记忆协议（Lesson 经验闭环）

在正向固化之外，还有一条轻量的经验沉淀通道：

```text
会话开始 (session start):
  1. lesson query --area <当前任务领域>     → 加载近期相关教训（避免重复踩坑）
  2. 读取相关实体 / 固化流程上下文
  3. 结合教训与上下文开始任务

会话结束 (session end):
  1. 从对话提取持久事实 → 沉淀经验进 Wiki（Lesson 属低风险，无需 Workflow 审批）
  2. 失败 / 返工复盘 → lesson record --outcome negative（沉淀教训）
  3. 验证有效的新方法 → lesson record --outcome positive（沉淀最佳实践）
  4. 更新相关实体与来源
```

Lesson 是低风险固化——只记录操作经验，不动口径、权限、数据源，可自动写回（对照 §4.5 固化审核表中的"负向知识页，低"），与 Workflow 固化（编译 + 人工审批 + 更新 Wiki 连接）是两条不同强度的通道。但要划一条红线：策略和授权不随会话沉淀。会话中出现的临时批准、破例放行只记录为待复审线索（`type: review_hint`），不写入 Lesson 的"做法"字段；策略更新必须走策略系统变更，授权续期必须显式操作（见 §6.9）。

经验库随会话持续累积，自主执行管线的失败率随使用时长下降，未命中路径的 token 消耗与返工成本随之降低——这就是复利的日常形态。

## 9. 安全与治理

治理这条线，可以按"谁能看什么、什么能自动执行、出了事怎么查、系统怎么自保"四组来看。

**谁能看什么**：权限不因"拼好了 SQL"而豁免——跨系统执行必须通过各系统鉴权，租户、部门、字段、行级权限由后端执行；SQL 强制走模板，LLM 只填参数、不造 SQL，参数经 Ontology 实体白名单校验；Context Object 同样受 RBAC 约束，能看到实体不等于能读状态。

**什么能自动执行**：涉及写入、付款、发消息等动作需审批或人工确认（流程内 `human_approval` 节点）；Workflow 固化必须人工审批——六步管线执行确认无误后按执行轨迹构图，只有审批通过才允许更新 Wiki 固化流程注册表，审批未过不产生任何命中路径，杜绝"错误图被自动固化"；固化流程条目强制携带适用域声明 `applicable_domain`（对象类型 + 状态范围 + 参数边界，§4.5/§6.5），命中执行前做超域检查，超出适用域自动降级 supervised 并告警；按复杂度分档决定自主度与监控频率（§4.4 表 + 动态自主度调节），运行期以实时异常告警 + 检查点 + 事后审查为主，避免逐级审批疲劳化。

**出了事怎么查**：审计记录强制包含六字段——who（用户+角色）、when（毫秒时间戳）、which rule（规则 ID+版本）、which schema（对象/动作 ID+版本）、before-after（关键字段值变化）、source（触发源系统）；审计只允许 INSERT、禁止 UPDATE/DELETE（append-only），独立于业务存储，并与 schema 版本联动，规则变更后旧审计仍可按版本解析——三个月前为什么这么改，答得上来。每次查询保留原始问题、映射路径、SQL、系统返回、最终结论；每次固化保留审批人、审批意见与版本变更记录；每次 Context 更新保留来源与触发事件。审计写入主体与业务写入主体分离，关键记录使用哈希链或 WORM 存储；审计写失败时高风险动作失败或进入人工接管，日志和 Context 中的敏感字段按数据分类脱敏。定期巡检（Lint）周期性检查孤立页面、冲突说法、失效链接、过期结论，workflow 的死分支、孤立节点与失效兜底策略，以及 Context 状态漂移（与源系统不一致）。

**系统怎么自保**：所有记忆写入先判定 `preference / fact / policy / authorization` 四类，policy 只存引用、authorization 绑定 scope 并默认过期，检索时做作用域与时效校验（见 §6.9）；Context / Lesson 写入前做反投毒审查——只接受 schema 白名单字段，拒绝指令性文本（提示注入），外部来源（网页 / 邮件 / 工单原文）与内部结构化状态分区隔离存储，防止"数据通道变指令通道"；幂等键至少绑定 `tenant_id + subject + workflow_id + action_id + target_object + operation + business_window`，重复请求返回原执行结果，补偿请求使用独立的补偿键，禁止依赖自然语言或时间戳单独去重；Ontology/策略不兼容、指标口径失效、数据源结构变化、Golden 回归失败、安全指标越界或关键 Context 被撤回时，Workflow 自动标记 `paused` 或 `revoked`，阻断新实例并保留运行中实例的处置记录；原始 SQL、工具返回和 Context 按数据分类加密、脱敏并设置保留期，审计记录与业务数据分离保存——删除请求只处理业务副本，审计保留最小必要字段并保留合规链路。

四层持续评估（§4.7）本身也是治理设施：生产 trace 中人工纠正的坏例子必须回流黄金集，作为换模型、改 prompt、换工具前的回归门禁。

白皮书的八项可信原则在本架构中都有落点：上下文前置 → ContextPack（§3.2-5）；可追溯 → 审计 6 字段（本节）；人在回路 → human_approval / 固化审批（§4.5）；规则与模型分工 → SQL 模板白名单（本节）；签核 → 分级审批；白名单 → Ontology 实体/参数校验（§4.3）；失败安全 → Saga 补偿 + MANUAL_REVIEW（§4.3-4）。

## 10. 风险与边界

没有架构是零风险的，诚实地列出已知风险与缓解手段，是治理的一部分：

| 风险 | 说明 | 缓解 |
|---|---|---|
| 映射表爆炸 | 自然语言别名无限，穷举不可行 | 映射表只维护封闭域（区域/指标/期间），开放表达交 LLM 兜底 |
| 错误固化 | LLM 把错误认识编译成 Workflow | 确认信号 + **固化人工审批** + 定期 Lint |
| 规则过期 | 固化模板口径漂移 | 版本 + 生效时间 + 失效机制 |
| 覆盖度有限 | 命中路径只覆盖已过审 Workflow | 混合策略：注册表优先，动态编排兜底 |
| 审批瓶颈 | 固化依赖人工审批，可能延迟知识沉淀 | 审批分级（低风险自动、高风险管理员）；审批队列与 SLA |
| 编排复杂度 | 节点 / 分支过多导致编排难以维护、失败难以定位 | 成功路径编译固化 + 定期 Lint 检查死分支与孤立节点 |
| Context 状态漂移 | Context 与源系统状态不一致，Agent 基于过期状态决策 | Lint 定期校准；物化带来源与 valid_at；高频对象可订阅实时流 |
| Context 膨胀 | 无节制物化导致状态仓库膨胀、查询变慢 | 只物化"决策所需的最小对象集合"；历史快照分级压缩归档；TTL 保留期 |
| 跨系统口径不一致 | 同一指标多系统定义不同 | Ontology 指定指标版本后统一下发 |
| Ontology 过度扩张 | 长期停留在字段讨论 | 一个真实任务驱动最小实体集合 |
| 授权放大 | 临时放行被压缩成常设权限，绕过审批 | 记忆类型分类：authorization 限 scope + 默认过期；放行规律只作复审线索（§6.9） |
| 审批疲劳 | 逐级审批形式化，高危变更被"习惯性批准" | 分级自主 + 里程碑检查 + 运行监控 + 事后审查（§4.4） |
| 上下文腐烂 / 中段丢失 | 物化结果全塞 prompt，长上下文稀释关键信息 | ContextPack 四原语组装：持久化→筛选→压缩→隔离（§3.2-5） |
| 记忆投毒 | 外部文本以指令形式写入记忆，操纵后续行为 | 写入反投毒审查 + 外部来源分区隔离 + schema 白名单字段（§9） |
| 评估盲区 | 只评答案对错，轨迹绕路 / 越权不可见 | 四层评估：L2 Trajectory 轨迹三问 + 坏例回流黄金集（§4.7） |
| 上下文过期 | 状态记忆无保留期，Agent 用陈旧 Context 决策 | Context / Lesson 按类型 TTL 保留期 + 失效机制（§3.2-6） |
| 外部副作用失败 | 跨系统写入类动作部分成功、部分失败，状态不一致 | Saga 异步补偿（PENDING→COMMITTED/ROLLBACK 逆序补偿）+ 幂等键，不采用 2PC（§4.3-4） |
| 审计被篡改/残缺 | 审计与业务同库同生共死，或字段缺失答不上追溯问题 | 审计 append-only 独立存储 + 强制 6 字段 + 与 schema 版本联动（§9） |

## 11. 落地路线图

先说排序原则。参考乌圆AI 自研引擎的 MVP 实证，能力按"数据规模 vs 决策治理"二分：**治理能力优先建齐**——审计、审批、权限、Saga 补偿，这是一个不能砍的灵魂，差距靠设计对齐；**数据规模能力后置 Phase 2**——图数据库、图检索、流式摄入，差距靠扩容补齐，不阻塞 MVP 闭环。6 周 MVP 最小闭环规划见 [research/mvp-plan.md](research/mvp-plan.md)。

落地节奏分三个阶段。第一阶段打地基：选定场景（经营分析 / 售后工单 / 采购询价，高频、数据清晰、结果可验证），建立最小实体集合（区域、客户、产品、订单、指标、工具六类），为实体建立唯一 ID、别名、定义、关系、来源、版本、负责人、验证状态，注册工具与模板（工具记录能力、参数类型、权限、风险、返回结构、失败处理，沉淀高频 SQL 模板）。第二阶段跑通链路：把映射、消歧、绑定、执行、物化、汇总接入 Ontology 与工具目录，先跑通未命中六步管线（全程 RBAC、参数白名单、步骤级审计，保留执行轨迹）；搭建 Context 层与 ContextPack 组装——为高价值对象定义 Context Object 的状态/事件/历史 schema，接入上下文物化、行动后回流，实现组装器（预算裁剪 + 动态工具注入 + trace_id），LLM 只读组装后的包。第三阶段建护栏、进闭环：先建四层评估基线（Golden Dataset 正负样例 + L1 Outcome 评测 + L2 Trajectory 轨迹三问 + L4 CI/CD 回归脚本，换模型 / 改 prompt / 改护栏前自动回归、失败即阻断）——注意评估基线要先于固化审批；再启用分级自主与运行监控（按复杂度分档设自主度 + 里程碑检查点 + 实时异常告警 + 事后审查，接入确认信号与澄清请求机制），启用固化审批（确认无误后按执行轨迹构图 → 人工审批 → 过审后更新 Wiki 注册表，审批未过不写 Wiki）；进入持续运营后，生产 trace 坏例回流 Golden，"多次放行"规律触发策略复审（不自动升级权限，见 §6.9），增量更新、定期 Lint（含 Context 状态漂移校准）、版本失效、TTL 巡检成为例行工作。

### 11.1 参考实现映射

本设计已有可运行参考实现 **[ontology-enterprise](https://github.com/qq450770953/ontology-enterprise)**（Python CLI + SQLite）。设计概念到实现能力的映射如下。先说清楚证据状态：表中"实现能力"分为**已运行验证**、**静态实现待端到端验证**和**规划/待扩展**三类，参考实现映射不等于本架构全部能力已经具备。

| 本设计章节 | 设计概念 | 实现能力 |
|---|---|---|
| §3 语义层 | Ontology 实体（唯一 ID / 别名 / 关系 / 口径版本 / 生效时间 / 权限约束） | `type define`（schema：required/enum/type）、`object create/get/query/update/delete`、`object alias-add/resolve`（namespace 消歧）、实体 version/effective_from/effective_to |
| §3 语义层 | 关系与约束（from/to 类型、基数、环） | `link relate/related`（cardinality=many_to_one、acyclic 环检测） |
| §4 命中路径 | 直接调用已固化 Workflow（注册表指向） | `state transition`（状态机合法流转）+ `method run`（白名单沙箱确定性计算），配合 LangGraph 加载已注册图 |
| §3.1 Workflow 编排层 | 固化按执行轨迹构图；命中加载已固化图；状态/重试/降级/审计 | LangGraph（图编排、状态持久化、断点续跑）；ontology-enterprise `method run`（确定性节点沙箱）、`action run`（前置条件 / 幂等 / 副作用 / risk）、`audit`（步骤级审计） |
| §4 未命中路径 | 六步管线执行 + 受治理动作（前置条件 / 权限 / 幂等） | `action register/run`（preconditions、required_role、idempotency-key、side_effect、risk） |
| §3.2 上下文层 | 上下文物化、状态/事件/历史建模、因果链 | `state transition`（对象合法状态流转）+ `object`（关联实体）+ `method run`（物化校验确定性计算）+ `lineage`（Context 来源追溯） |
| §4.5 固化 | 按执行轨迹构图 → 人工审批 → 注册 | 审批队列 + `state transition` 状态约束；`supersedes_id`/status 支持替代与失效 |
| §9 安全与治理 | 权限校验、审计可追溯（6 字段 + append-only）、数据血缘 | `policy add/check`（RBAC）、`audit query`（全操作审计；**规划：audit 表升级 6 字段 + append-only**）、`lineage trace`（血缘追溯） |
| §6.9 记忆类型分类 | preference/fact/policy/authorization、授权 scope+过期、策略引用不复制 | 规划：`memory add --type <preference|fact|policy-ref|authorization>` + 检索作用域校验（ontology-enterprise 待扩展） |
| §4.7 四层评估 | Golden Dataset + Trajectory 三问 + CI 回归 | 规划：评测集（正负样例）脚本 + `audit query` 轨迹关联（ontology-enterprise 待扩展） |
| §3.2-5 ContextPack | 上下文组装：筛选/压缩/隔离 + trace_id | 规划：ContextPack 组装器 + 预算策略（参考实现待补充） |
| §8 知识复利 | 固化写回、版本失效 | SQLite 存储 + `--root` 隔离；实体 `supersedes_id`/status 支持替代与失效 |

分工上，LangGraph 承担 Workflow 编排层（图编排、状态、重试、断点续跑），ontology-enterprise 承担语义层与治理底座（实体、模板、受治理动作、RBAC、审计），二者组合即为可运行底座。"入口路由层（Wiki 索引匹配 / 固化流程注册表）"与"接入层（MCP/CLI/API 真实系统）"需按 §11 路线图在企业场景中接入。

## 12. 参考

- Andrej Karpathy, *LLM Wiki*（GitHub Gist, 2026-04）
- 凯哥探数, *AI时代的本体论：本体不是终点，实时上下文才是企业认知的生命线*（微信公众号, 2026-08）
- 凯哥探数, *Actionable Knowledge Architecture：知识架构的进化——从本体工程到企业 AI 智能体*（扫描件 PDF, 14 页, 2026-08，[本地副本](references/Actionable_Knowledge_Architecture.pdf)）——结论公式 `Ontology（定义世界）+ Context（当前状态）+ LangGraph（编排行动）= Enterprise Autonomous Agent` 与本架构核心一致
- W3C, *Web Ontology Language (OWL)*
- Patrick Lewis et al., *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks* (2020)
- Shunyu Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models*
- LangChain, *LangGraph*（有状态、图编排的 Agent 运行时）
- Model Context Protocol, *Understanding MCP Servers*
- 乌圆AI, *自研 Ontology Engine 最小规格：5 大模块 + Action Engine 7 步链路*（微信公众号, 2026-07-26）——**v1.6 主要优化来源**：Action Engine 7 步治理链路（Param Validate→Permission→Rule→Internal Mutate→Saga Side Effect→Audit→Rollback）、审计 6 字段 append-only、Saga 补偿替代 2PC、"数据规模 vs 决策治理"二分法（借鉴映射详见 [research/mvp-plan.md](research/mvp-plan.md)）
- yeasy, *智能体 AI 权威指南（agentic_ai_guide）* v1.4.0, 2026-08, CC BY-NC-SA 4.0（GitHub 开源书）——**v1.5 主要优化来源**：其 §3.6 上下文工程与记忆护栏、§7.3 轨迹分析、§9.7 反模式（Dark Code / 渐进扩展 / 护栏先行 / TCO）、§9.9 分级授权实证
- 《智能体赋能汽车研发设计白皮书——从工具辅助到智能原生》（2026，中国汽研牵头、一汽/长安/理想等 15 家企业联合发布）——**v1.8 主要优化来源**：运行时动态降级（§4.4）、适用域声明与超域降级（§4.5/§6.5/§9）、技能资产治理（§6.5）、场景四维评估矩阵与数据治理先行（[mvp-plan](research/mvp-plan.md) v1.2）、A/B 对照测试（§4.7）、token 计量"智能量"展望（§7）；其"通用模型/垂域模型/机理模型三层分工"印证本架构"确定性节点不调用 LLM"原则
