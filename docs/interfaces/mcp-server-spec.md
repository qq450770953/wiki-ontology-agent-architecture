# 本体只读服务出口（Ontology Service Egress）接口规约

> 版本：v0.1（2026-10-26）｜ 状态：接口契约 + 原型实现
> 动机：Neo4j Context Engine 证明「本体独立成层」的分界线是**独立进程 + 标准协议出口**，不是建模能力。本规约给 `ontology-enterprise` 定义三个只读能力，使第二个 Agent 能不 import 代码就读到本体。
> 原型位置：[`prototype/mcp_server.py`](../../prototype/mcp_server.py)（HTTP JSON-RPC 调试接口）

## 0. 协议边界（重要声明）

**本规约 v0.1 的原型使用 HTTP + JSON-RPC 2.0 作为传输层，属于调试接口简化实现，不是 MCP（Model Context Protocol）标准传输。** 与标准 MCP 的差异：

| 项 | 本原型（v0.1） | MCP 标准 |
|---|---|---|
| 传输 | HTTP POST（`localhost` 默认 3100） | stdio / Streamable HTTP（官方 `mcp` Python SDK） |
| 发现机制 | `rpc.discover` 自定义方法 | `resources/list` / `tools/list` |
| 鉴权 | 无（仅绑定 127.0.0.1） | OAuth 2.1 / 传输层安全 |

升级到标准 MCP 的路径：三个能力的**入参/出参 schema 不变**，仅替换传输与发现层。`resources/` URI 映射见 §4。在设计上先冻结 schema、后换传输，是刻意选择。

## 1. 设计约束（不可豁免）

1. **只读**：三个能力均不产生写入；本体变更仍走 §3.5 发布门禁（候选→校验→审批→冻结→激活）。
2. **作用域过滤**：所有返回先按 `scope`（租户/主体）过滤，越权返回 `UNAUTHORIZED`，**不得返回存在性信息**。
3. **版本精确**：响应必含 `ontology_version`；不提供 `latest` 模糊解析（对应 §3.6「禁止解析 latest」）。
4. **禁止动态 SQL**：服务端不接受任何查询语句文本，只接受结构化参数。
5. **审计**：每次调用写 audit 记录（who/when/which method/params 摘要/result 码）。原型阶段以 stdout 日志替代，字段对齐 §9 六字段。
6. **限流**：原型实现简单令牌桶（默认 10 req/s），超出返回 `RATE_LIMITED`。

## 2. 能力一：`schema.describe` — 读取本体结构

**用途**：Agent 启动时发现「这个世界由什么构成」：类型、属性、关系、基数、版本。

**请求**
```json
{"jsonrpc": "2.0", "id": 1, "method": "schema.describe",
 "params": {"ontology_version": "2.3.0", "scope": {"tenant": "t1", "subject": "agent.demo"}}}
```

**响应（成功）**
```json
{"jsonrpc": "2.0", "id": 1,
 "result": {"ontology_version": "2.3.0",
   "types": [{"name": "Device", "properties": {"device_id": "string", "station": "ref:Station"},
              "relations": [{"name": "has_fault_code", "to": "FaultCode", "cardinality": "many_to_many"}]}],
   "profile_ref": "profile.manufacturing.equipment-fault@1"}}
```

**错误码**：`VERSION_UNAVAILABLE`（版本不存在）、`UNAUTHORIZED`、`SCHEMA_MISMATCH`（请求版本与服务端冻结版本不一致）。

## 3. 能力二：`object.query` — 按类型查询实例

**用途**：按类型 + 过滤条件读实例。只接受结构化过滤，不接受自由文本。

**请求**
```json
{"jsonrpc": "2.0", "id": 2, "method": "object.query",
 "params": {"type": "FaultCode", "filter": {"code_prefix": "E1"}, "limit": 20,
            "scope": {"tenant": "t1", "subject": "agent.demo"},
            "ontology_version": "2.3.0"}}
```

**响应**
```json
{"result": {"ontology_version": "2.3.0", "total": 2, "objects": [
  {"id": "fault.e102", "type": "FaultCode", "properties": {"code": "E102", "severity": "high"},
   "effective_from": "2026-01-01", "status": "verified"}]}}
```

**约束**：`limit` 上限 100；结果不含无权字段；`total` 为过滤后计数（先鉴权再计数，防侧信道枚举）。

**错误码**：`UNAUTHORIZED`、`TYPE_NOT_FOUND`、`VERSION_UNAVAILABLE`。

## 4. 能力三：`context.assemble` — 组装上下文包骨架

**用途**：把与某意图相关的对象/指标/工具引用按预算组装为 ContextPack 骨架（§3.2-5 四段结构），供 LLM 消费。**只组装已授权内容**，每条带来源与裁剪原因。

**请求**
```json
{"jsonrpc": "2.0", "id": 3, "method": "context.assemble",
 "params": {"intent": "查询工位 A3 当前故障码处置",
            "budget_tokens": 1500, "expand_hops": 1,
            "scope": {"tenant": "t1", "subject": "agent.demo"},
            "ontology_version": "2.3.0"}}
```

**响应（对齐 ContextPack 四段）**
```json
{"result": {"trace_id": "ctx_7f3a", "ontology_version": "2.3.0",
  "system": "role=analysis_agent; tenant=t1",
  "tools": [{"ref": "tool.fault.lookup", "risk": "L1", "cut_reason": null}],
  "memory": [{"type": "policy", "ref": "policy.mes.readonly@2", "cut_reason": null}],
  "evidence": [{"object_id": "fault.e102", "source": "mes.gateway", "observed_at": "2026-10-26T09:00:00+08:00",
                "trust": "observed", "token_count": 120, "cut_reason": null}],
  "dropped": [{"object_id": "metric.oee", "cut_reason": "budget_exceeded"}]}}
```

**约束**：`expand_hops` 上限 2（沿 `maps_to`/`applies_to` 关系）；`dropped` 必须带 `cut_reason`（可回答「为什么这次回答长这样」）；未授权内容进入 `dropped` 时 `cut_reason=unauthorized` 且**不泄露对象 ID**。

**错误码**：`UNAUTHORIZED`、`BUDGET_TOO_SMALL`（<200 tokens）、`VERSION_UNAVAILABLE`。

## 5. 通用错误码

| 码 | HTTP 状态 | 语义 |
|---|---|---|
| `UNAUTHORIZED` | 403 | 作用域无权；不区分「无权」与「不存在」 |
| `VERSION_UNAVAILABLE` | 404 | 请求的本体版本未冻结或已撤销 |
| `TYPE_NOT_FOUND` | 404 | 类型不存在（在授权范围内） |
| `SCHEMA_MISMATCH` | 409 | 请求 schema 与服务端冻结版本冲突 |
| `RATE_LIMITED` | 429 | 超出限流 |
| `INVALID_PARAMS` | 400 | 参数不符 schema |

## 6. 验收标准（P0-1）

1. **并发一致性**：两个独立进程（curl + Python 客户端）同时 `schema.describe`，返回**相同** `ontology_version` 且类型集合一致。
2. **越权拒绝**：`scope.subject` 未注册时返回 `UNAUTHORIZED`，响应体不含任何对象 ID。
3. **只读性**：全部方法无副作用；重复调用幂等（同请求同响应，除 `trace_id`）。
4. **审计留痕**：每次调用在日志中产生一条含 who/when/method/result 的记录。
5. **裁剪可解释**：`context.assemble` 的每个 `dropped` 条目有 `cut_reason`。

原型验收记录见 [`prototype/mcp_server.py`](../../prototype/mcp_server.py) 文件头「验收记录」一节（含实际执行日期与输出摘录）。

## 7. 与标准 MCP 的映射（升级路径）

| 本规约能力 | MCP resource URI（升级后） | MCP 模式 |
|---|---|---|
| `schema.describe` | `ontology://{version}/schema` | Resource（只读） |
| `object.query` | `ontology://{version}/objects/{type}` | Resource Template |
| `context.assemble` | `context://assemble` | Tool（有计算，非纯读取） |

## 8. 明确不做（v0.1）

- 写入类方法（`object.create` 等）——本体变更走发布门禁，不开放运行时写入。
- 跨版本 diff、订阅/推送（CDC）。
- 多租户真实鉴权体系（原型用注册表 mock）。
- 图数据库后端（SQLite/YAML 文件即可支撑三能力）。

## 9. 来源与依据

- Neo4j Context Engine 官方博客（四接入面：headless/CLI/Python/MCP），2026-10。
- 本仓 `architecture.md` §3.6（四层权限、版本冻结）、§9（审计六字段）、§3.2-5（ContextPack 四原语）。
- 对标评审报告：WorkBuddy 工作区 `ontology-vs-neo4j-context-engine-review.html`（2026-10-26）。
