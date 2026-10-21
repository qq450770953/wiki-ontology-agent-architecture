# Plan: LLM-Wiki 图谱索引与数字世界 VLA 资产治理 (from accepted intent/spec 2026-10-21)

## Files that change

1. `docs/architecture.md`
   - 升级版本至 v2.2，并在版本块增加数字世界 VLA 与 LLM-Wiki 图谱索引的增量说明。
   - 在总体架构/组件职责中明确 LLM-Wiki 的定位：资产索引、知识入口和治理投影；不替代 Ontology Registry、Policy Service、Tool/Connector Registry、Workflow Registry、Context Store 和外部业务系统。
   - 新增“LLM-Wiki 图谱索引与数字世界 VLA 资产治理”章节，覆盖：
     - 顶层节点类型与受控边类型；
     - Semantic、Knowledge、Policy、Capability、Workflow、Exploration 六类逻辑索引；
     - 知识、可执行资产和探索资产的生命周期；
     - 发现/读取/执行/发布四层权限与三次授权检查；
     - Concept → Tool Contract → Connector → External System → Context → Verification 的工具链接；
     - Workflow Registry 权威、依赖版本冻结、发布门禁和回滚边界；
     - 未知探索图谱与候选资产 Promotion 规则；
     - Wiki Graph Index 到 TaskRun/Context/Artifact 的运行时查询链路；
     - 验收标准和未决选型边界。
   - 在数据模型章节增加 Wiki Graph Node/Edge、索引分片和权限策略引用示例，并与既有 Workflow、Context、Ontology 模型建立引用关系，避免重复定义。
   - 修正已发现的完成证据矛盾：不得将模型最终汇总等同于外部业务回执；`unknown` 必须回查，业务完成必须有外部回执或独立验证证据。
   - 在风险与路线图中增加图谱索引、策略引擎、图存储、视觉输入和首个数字场景的待确认项；P0 采用受审查清单/确定性索引和单一低风险只读试点，不承诺已实现运行时。

2. `README.md`
   - 更新当前设计版本与核心设计摘要。
   - 在仓库结构、快速阅读指南和核心架构说明中加入新的研究/规范入口（如新增研究文档存在时）。
   - 明确当前仍为架构设计文档仓，LLM-Wiki 图谱索引为设计契约，不代表运行时已经实现。

3. `docs/research/llm-wiki-graph-index-design.md`（新增）
   - 保存完整的 LLM-Wiki 图谱索引规范，作为 architecture.md 的详细设计引用。
   - 内容以已批准的 `.sdlc/digital-world-vla/spec.md` 为来源，保留公共节点契约、边类型、权限模型、工具链接、Workflow、探索 Promotion、运行时链路和验收标准。
   - 标注当前为设计契约，未进行生产系统、真实连接器或图数据库验证。

4. `.sdlc/digital-world-vla/intent.md`
   - 保持已批准状态，不改动问题、结果、约束和开放问题内容；如需要，仅在实施阶段附加批准/实施关联信息。

5. `.sdlc/digital-world-vla/spec.md`
   - 将状态从 `draft` 更新为 `accepted (2026-10-21)`，保留用户批准记录；不在实施阶段悄悄改变已批准设计。

## Order of work

1. 读取并核对主架构的章节目录、版本块、组件职责、数据模型、风险和路线图，确定插入位置，避免破坏 ASCII 图和现有锚点。
2. 新增 `docs/research/llm-wiki-graph-index-design.md`，将已批准规范转为项目研究文档，补充与现有 Ontology/Context/Connector/Workflow 契约的引用边界。
3. 更新 `docs/architecture.md`：先改版本块，再补总体职责和新章节，随后补数据模型、完成证据矛盾修正、风险和路线图；不新增实现代码。
4. 更新 `README.md` 的版本摘要、仓库结构和快速阅读入口。
5. 对新增和修改内容做一致性审查：术语、状态枚举、版本引用、权威真源、权限和 `unknown` 处理规则不得互相矛盾。
6. 运行只读验证：Git diff/status、Markdown 链接与文件存在性检查、关键术语计数、ASCII 架构图相关行不变性检查；不声称运行时测试通过。
7. 形成变更摘要和剩余开放问题，等待用户决定是否需要提交/推送到 GitHub。

## Risks

- 主架构文档已有较多版本增量，新增章节可能造成目录、锚点或版本说明遗漏。
- `architecture.md` 包含 ASCII 架构图，编辑时不能重排空格或改动无关行。
- Wiki 索引与 Registry 真源边界若表述不清，可能误导为已存在运行时服务；文档必须显式标注“设计契约、未实现”。
- 当前尚未确认图数据库、策略引擎、多模态输入和首个数字业务试点，不能把选型或真实连接写成已验证事实。
- 完成证据规则的修正可能影响既有 TaskRun 示例，必须保持 `accepted/running/succeeded/failed/unknown` 语义兼容。
- 新增研究文档需要同步 README 与 architecture.md 引用，避免死链。

## Proof

- `git diff --check` 通过，确保无明显空白错误。
- `git status --short` 仅出现预期的 `.sdlc` 产物、`README.md`、`docs/architecture.md` 和新增研究文档。
- 用只读脚本检查：新增研究文档存在、README/architecture.md 引用路径存在、关键节点类型/六类索引/四层权限/unknown 回查/外部验证等术语可检索。
- 检查 `architecture.md` 中 ASCII 架构图代码块的行内容与本次改动前一致，确认未发生对齐破坏。
- 检查版本块为 v2.2，并包含明确的增量说明。
- 逐项对照已批准 spec 的验收标准，输出“文档契约已覆盖/尚未实现”的矩阵；不把文档存在等同于运行时验证。

## Deviations

None.

## Addendum（2026-10-21）：决策层接入降级为 TODO

原计划未包含决策模型。实施完成后，用户要求将 AgentJev / Decision Gate 明确后置，因此补做以下改动（架构版本 v2.2 → v2.3）：

- `docs/architecture.md`：新增 §3.7「决策门控层（Decision Gate / AgentJev）—— TODO，本期不实施」与 §11.2「后续阶段 TODO（本期不实施）」清单（T1–T6）。
- `docs/research/llm-wiki-graph-index-design.md`：新增 §12 后续 TODO，记录待定项、约束与启动条件。
- `README.md`：版本号与导航同步至 v2.3。

范围约束（本次严格遵守）：

- 只写「放在哪里、受什么约束」，不写模型选型、训练方案、阈值与评测方案。
- 不新增运行时代码，不声称任何决策层能力已实现。
- 不改变 TaskRun / Action 状态语义，不绕过 Policy Service 与外部回执验证。
