# Wiki + Ontology 架构 MVP 落地规划（6 周最小闭环）

> 版本：v1.1（2026-09-05）
> 状态：MVP 规划 + 外部方案借鉴映射（对应 architecture.md v1.7）
> 读者对象：企业知识库 / AI Agent 平台的技术负责人、架构师（本仓库 owner 健哥）
> 目的：回答「参考乌圆AI 自研 Ontology Engine 的 MVP 方案，本架构如何以最小代价跑通第一个闭环」
> 相关：`docs/architecture.md`（v1.7）§4.3 六步管线 / §9 安全治理 / §11 落地路线图 / §11.1 参考实现映射

## 1. 来源与结论摘要

来源：乌圆AI《自研 Ontology Engine 最小规格：5 大模块 + Action Engine 7 步链路》（微信公众号，2026-07-26）。该文核心论点：

- **自研本体引擎 = "1 个不能砍 + 4 个能砍"**：5 大模块（Schema Registry / Object Store / Object Set Service / Action Engine / Funnel）中，**Action Engine 是治理灵魂绝对不能砍**，其余 4 个是"数据规模问题"，可后置 Phase 2。
- **数据规模问题 vs 决策治理问题二分**：前者（图 DB / 图检索 / 流式摄入）差距靠扩容补齐；后者（事务 / 审计 / 血缘 / 权限）差距靠设计对齐，8 周即可对齐 Palantir 基础治理。
- **Action Engine 7 步链路**：Param Validate → Permission → Rule Evaluate → Internal Mutate（ACID）→ Side Effect（Saga）→ Audit Write（append-only）→ Response / Rollback；步骤 2/3/6 不可绕过。
- **避坑**：死磕 2PC 是弯路（用 Saga 异步补偿）；`user edits always win` 冲突策略；业务方必须半职参与否则必失败。

对本架构的判断：**本文只覆盖"引擎"层，不含知识闭环与 Context——本架构的差异化价值（轨迹→审批→Wiki 注册→命中复利）恰好补上这块**。二者互补，可直接吸收其工程护栏（见 §2）。

## 2. 借鉴映射：4 个采纳点（已落 architecture.md v1.7）

| # | 借鉴点 | 文章原始设计 | 本架构落位 | 状态 |
|---|---|---|---|---|
| 1 | **审计 6 字段 + append-only** | who / when / which rule(版本) / which schema(版本) / before-after / source；只许 INSERT、独立存储、与 schema 版本联动 | §9 "审计可追溯"条目升级；§10 新增"审计被篡改/残缺"风险行；§11.1 audit 表升级规划 | ✅ 已落 |
| 2 | **Saga 补偿替代 2PC** | 外部副作用走 Saga 异步补偿（PENDING→COMMITTED/ROLLBACK 逆序补偿）+ 幂等键 | §4.3-4 "各系统执行"补写入类动作 Saga 补偿；§10 新增"外部副作用失败"风险行 | ✅ 已落 |
| 3 | **user edits always win** | 人工/Action 改过的属性不被源系统再同步覆盖 | §4.3-5 上下文物化写入冲突策略 | ✅ 已落 |
| 4 | **数据规模 vs 决策治理二分排序** | 治理能力优先建齐、规模能力后置（真实案例：砍 OSS+Funnel，8 周跑通 1 场景） | §11 路线图开头新增排序原则引言 | ✅ 已落 |

**未采纳 / 已领先部分**：文章的 Funnel CDC 摄入、GraphRAG、行列级 Marking、Branching 均归入 Phase 2（与本架构一致）；本架构的 Wiki 知识闭环、Context 物化、ContextPack 组装、分级自主矩阵、四层评估为文章未覆盖的领先设计，MVP 中保留。

## 3. MVP 目标与范围

**目标：跑通 1 个场景的完整闭环**——未命中 → 治理执行 → 物化 → 人工审批 → Wiki 注册 → 下次命中直调。**不以"建全八层"为目标，以"闭环转起来"为目标**（呼应文章"先造能在村里跑的车"）。

### 3.1 MVP 模块范围（对标文章 5 大模块 × 本架构八层）

| 本架构模块 | MVP 形态 | 对标文章模块 | 必要性 |
|---|---|---|---|
| 语义层（Ontology） | YAML 定义 ≥3 个核心 Object + Link + Action 类型（基于 ontology-enterprise） | Schema Registry | 必有 |
| 上下文层（Context） | SQLite objects/links/properties/audit 起步 | Object Store | 必有 |
| 执行层 | 六步管线 × Action 7 步融合链路 | **Action Engine** | **核心，工时占 50%** |
| 治理层 | 审计 6 字段 append-only + 人工审批闸口 + RBAC（简陋版） | Action Engine 治理部分 | 必有 |
| 知识层（Wiki） | 审批过的固化流程注册表（YAML/JSON）+ 命中直调 | —（本架构独有） | 必有（闭环的关键） |
| 编排层 / 接入层 | CLI 入口简化；LangGraph 构图 W6 后引入 | Orchestration | 简化 |

### 3.2 明确不做（Phase 2）

图 DB / GraphRAG、CDC 流式摄入（Funnel）、行列级 Marking 权限、Branching、多源自动 merge、LangGraph 动态构图（固化管线先行，动态构图等闭环跑通再上——遵守"先执行后构图"约定）。

## 4. 六周排期

| 阶段 | 内容 | 产出 |
|---|---|---|
| **W1-2 语义底座** | YAML 定义核心 Object/Link/Action 类型；audit 表升级为 6 字段 append-only（借鉴点 1/4 落地） | Schema 注册表最小版 + 合规审计表 |
| **W3-4 执行链路** | 六步管线 × Action 7 步融合：治理三道门（参数/权限/规则）→ 内部事务 → 外部 Saga 补偿（借鉴点 2）→ 审计；物化加 user edits win（借鉴点 3） | 可执行、可审计、可补偿的 Action 链路 |
| **W5 闭环沉淀** | 上下文物化 → LLM 汇总（读 ContextPack）→ 人工审批闸口 → 过审流程注册 Wiki 注册表 + 命中直调 | 知识闭环可运转 |
| **W6 试运行** | 1 个真实场景跑 7 天 + 业务验收 | 闭环验证 |

**首选试点场景：设备售后知识库的"故障码 → 处置动作"**——与本仓库 owner 的 device-station-fault（device-station-fault code-cause-step 层级）天然结合：Object = 设备/工位/故障码/处置步骤，Action = 处置建议下发与工单回写，审批闸口 = 处置步骤入库审核。备选：MES 经营数据查询（architecture.md §1 原型场景）。MVP 明确不做：自动执行高危设备控制、无人工确认关闭工单、跨租户查询、以未确认因果链直接驱动写入。

## 5. 验收标准（6 周核心闭环，另加 2 周试运行与验收）

✅ 语义底座跑通（≥3 个核心 Object + Link + Action 类型）
✅ ≥1 个 Action 在生产环境跑通（含 6 字段审计 + Saga 补偿演练）
✅ 1 条流程经"执行轨迹 → 人工审批 → Wiki 注册"完成固化，同类问题命中直调
✅ 决策血缘可见：任何一次结果可按 trace_id 回溯到 who / which rule / before-after / source
✅ 业务方能说清"这个引擎能帮我做什么"
✅ Context 状态区分 observed / inferred / confirmed / retracted，未经确认的结论不会进入正式决策区
✅ Workflow 发布具备版本冻结、静态检查、灰度、撤销和回滚记录
✅ Saga 补偿失败可进入人工接管，审计写入失败不会静默放行高风险动作

❌ 不产出（Phase 2）：GraphRAG / 社区摘要、流式摄入、行列级权限、Branching、动态构图自动化。

## 6. 与 §11 落地路线图的关系

本文档是 architecture.md §11 路线图的**执行切片**：取其步骤 1-2（场景 + 最小实体集合）+ 步骤 5-6（六步管线 + Context）+ 步骤 9（固化审批）构成 6 周核心闭环；W7-W8 用于试运行和业务验收，不计入核心实现周期。步骤 7（四层评估基线）在 W5 一并建立最小版（Golden 正负样例 ≥10 条），步骤 3-4（映射表/工具目录全量建设）按需延后。排序遵循 §11 的“治理优先、规模后置”原则。

## 7. 参考

- 乌圆AI, *自研 Ontology Engine 最小规格：5 大模块 + Action Engine 7 步链路*（微信公众号, 2026-07-26）——本文 MVP 方案的主要外部参考
- 乌圆AI 系列前篇：《业务本体不是又一个知识图谱（KG）》《企业本体建模法：Palantir 4 要素 + DDD 4 原则 + 治理 3 步》《企业 AI 知识底座的 5 层架构》
- 本仓库 `docs/architecture.md`（v1.7）§4.3 / §9 / §10 / §11 / §11.1（4 个借鉴点落位）
