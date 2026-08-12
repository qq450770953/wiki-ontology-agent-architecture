# Wiki + Ontology 企业知识行动架构

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 让企业知识从"可检索"走向"可行动"：LLM Wiki 沉淀认识，Ontology 统一语义，确定性管线降低成本，反馈闭环实现知识复利。

本仓库是一份**架构设计方案**（非实现代码），面向企业知识库 / AI Agent 平台的技术负责人和架构师。方案回答一个核心问题：**如何让 Agent 基于企业知识正确选择工具、绑定参数并完成跨系统查询，而不是停留在"检索到相关文档"？**

## 核心设计

1. **入口先查记忆（Wiki 层）**：用户提问后先查已固化的流程（映射表、SQL 模板、历史成功路径）。命中 → 确定性复用，0 token、秒级响应、结果可审计。
2. **未命中再自主执行**：LLM 走五步管线——关键词映射 → Ontology 实体消歧 → 工具映射与参数绑定 → 跨系统执行 → 汇总解释。
3. **正向反馈后固化**：执行成功并经确认后，把正确的流程、映射、模板写回 Wiki，形成知识复利闭环——同类问题下次直接走命中路径。
4. **全程可治理**：指标口径、权限、版本、来源全部纳入 Ontology 与 Wiki 管理，错误固化有护栏，过期规则有失效机制。

## 架构总览

### 分层视图

![六层架构总览](docs/images/architecture-overview.svg)

六层职责一句话：**入口路由层**决定走哪条路，**知识层（Wiki）**负责记忆与沉淀，**语义层（Ontology）**统一业务语言，**执行层**区分确定性复用与自主执行，**接入层**访问真实系统，**治理层**横切保障安全与质量。

### 完整链路（双路径 + 反馈闭环）

![完整链路架构图](docs/images/pipeline.svg)

- **左侧（命中路径）**：确定性执行，0 token、秒级响应
- **右侧（自主执行管线）**：LLM 五步执行 → 正向反馈确认 → 总结固化回 Wiki
- **绿色虚线（回环）**：知识复利，固化后下次入口直接命中

## 仓库结构

```
wiki-ontology-agent-architecture/
├── README.md                    # 项目简介与快速导航
├── LICENSE                      # MIT License
├── CONTRIBUTING.md              # 贡献指南
└── docs/
    ├── architecture.md          # 完整架构设计文档（核心交付物）
    └── images/
        ├── architecture-overview.svg   # 六层架构总览图
        └── pipeline.svg                # 完整链路架构图
```

## 快速阅读指南

- 想了解整体方案：读 [docs/architecture.md](docs/architecture.md)
- 想理解双路径与闭环：看完整链路图 + architecture.md 第 4 章
- 想评估 token 成本：看 architecture.md 第 7 章
- 想落地实施：看 architecture.md 第 11 章落地路线图
- 想参与贡献：读 [CONTRIBUTING.md](CONTRIBUTING.md)

## 设计来源

本方案综合以下思想：Andrej Karpathy 的 LLM Wiki（知识编译与复利）、W3C OWL（本体语义）、Lewis 等人的 RAG（证据检索）、Yao 等人的 ReAct（推理-行动循环）、Model Context Protocol（工具接入），并将其组织为一条"记忆（Wiki）→ 语义（Ontology）→ 行动（工具）→ 复利（回环）"的完整链路。

## License

[MIT](LICENSE) —— 详见 [CONTRIBUTING.md](CONTRIBUTING.md) 中的贡献者许可说明。
