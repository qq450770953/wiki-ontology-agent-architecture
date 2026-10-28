# prototype/retrieval — 混合检索与评估原型

**定位**：验证「分块 → 多路召回 → 融合 → 重排 → 评估」这条管道在本仓语料上能真实跑通，并产出可复现的召回指标。
**不是**生产检索服务，**不是** RAG 系统，**不是** `ontology-enterprise` 的实现。

## 一、边界声明（对外表述时不得省略）

| # | 声明 | 说明 |
|---|---|---|
| 1 | 向量路是 **TF-IDF 哈希向量**，不是 embedding 模型 | 中文 char-bigram + 英文词 → 16 位 crc32 哈希桶 → L2 归一化。代码保留 `Embedder` 协议，换 bge-m3 / text-embedding-3 时只替换 `embed()` |
| 2 | 图谱路是**内存图**，不是 Neo4j | 术语边 + 章节相邻 + 文档内链；等价 Cypher 见 `pipeline.py` 末尾 `CYPHER_EQUIVALENTS` |
| 3 | 重排是**启发式**，不是 cross-encoder | 融合分 + 词项覆盖 + 相邻短语 + 标题覆盖；换 bge-reranker 时只替换 `rerank()` |
| 4 | 语料是**本仓库文档**，不是真实工单库 | 233 chunk / 14 文档，评估集 62 条人工标注。指标证明管道与评估可复现，不证明生产检索质量 |
| 5 | `HttpEmbedder` 的**协议行为已验证，模型未接** | 用本地 stub 服务验证请求构造/响应解析/维度校验/错误码；没有真实 embedding 权重参与任何指标 |

## 二、运行

```bash
# 指标
python prototype/retrieval/evaluate.py                # 五路消融对比
python prototype/retrieval/evaluate.py --by-category  # 按类别拆解（看哪类全灭）
python prototype/retrieval/evaluate.py --ablation     # 逐路留一法：每路是贡献还是噪声
python prototype/retrieval/evaluate.py --detail       # 逐查询明细
python prototype/retrieval/evaluate.py --out base.json
python prototype/retrieval/evaluate.py --compare base.json   # 与基线比 delta

# 数据与校验
python prototype/retrieval/evaluate.py --validate            # 只校验标注引用
python prototype/retrieval/evaluate.py --suggest "某问题"     # 辅助人工标注：看 top-10
python prototype/retrieval/evaluate.py --dump-chunks

# 换后端
python prototype/retrieval/evaluate.py --embedder http       # 需 EMBEDDING_BASE_URL

# 测试（76 个用例，零依赖）
python -m unittest discover -s prototype/retrieval -p "test_*.py" -v
```

无第三方依赖（仅标准库）。默认无网络访问；只有 `--embedder http` 才发请求。

## 三、实测指标（2026-10-28，Python 3.13.14，语料指纹 `f8d8f028c8479da6`）

```
corpus: 233 chunks / 14 docs | golden: 62 queries | index build: ~75 ms
两次运行指标逐位一致（p50 除外）

mode              R@1    R@3    R@5   R@10  MRR@10   top1   p50ms
bm25            0.548  0.790  0.823  0.871   0.738  0.629    0.24
vector          0.427  0.637  0.726  0.798   0.616  0.500    0.31
graph           0.024  0.048  0.105  0.258   0.089  0.048    0.03
hybrid          0.427  0.694  0.806  0.839   0.623  0.484    0.69
hybrid_rerank   0.427  0.710  0.815  0.839   0.630  0.484    1.73
```

`bm25` 指 BM25F（heading 权重 ×2）。融合权重为默认配置 `[1.0, 1.0, 0.35]`，**未针对评估集搜索最优**——62 条查询上搜权重必然过拟合。

按类别（bm25）：`mvp` 1.00 / `term` 0.67 / `evaluation` 0.67 / `connector` 0.64 / `contract` 0.60 / `ontology` 0.56 / `architecture` 0.40 / `api` 0.40 / `research` 0.00（n=1）。
`architecture` 与 `ontology` 的 R@1 明显低：这两类在 233 个 chunk 里各有 81 / 19 个候选，同类章节措辞高度相似，正样本容易被兄弟章节顶掉。

> **基线必须与语料指纹绑定**：语料包含本仓库文档，改一行文档就会改变 chunk 划分。`--out` 会把 `fingerprint` 写进结果，`--compare` 发现指纹不同时会直接提示「指标不可直接比较」，而不是把语料漂移误读成代码改进。

## 四、消融结论：融合在本语料上是**负收益**

`--ablation` 逐路留一法（RRF，权重同默认）：

| routes | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|
| bm25 | **0.548** | **0.823** | **0.871** | **0.738** |
| bm25 + graph | 0.403 | 0.726 | 0.855 | 0.602 |
| bm25 + vector | 0.508 | 0.774 | 0.847 | 0.686 |
| bm25 + vec + graph | 0.427 | 0.806 | 0.839 | 0.623 |

**每一路加进去都拉低指标。** 机制很清楚：RRF 只用名次、不用分数，于是图谱路的一条错误命中（rank 1）与 BM25 的一条正确命中（rank 1）拿到同样的 `1/61`，把准确路的 top-1 挤掉。

这不是「RRF 不好」，而是**融合的前提是每路都有独立信息量**。当前 vector 路与 BM25 共享同一 token 空间（都是 char-bigram 词项统计），提供的是重叠信号；graph 路精度只有 0.048，属于低精度噪声源。所以在真实 embedding 接入之前，本语料的正确默认是**单路 BM25F**，而不是 hybrid。

## 五、六条工程结论（含三次自我证伪）

### 1. BM25F 标题加权是唯一有确定收益的优化

标题已含全部关键词的查询（如「图谱节点有哪些类型」）在纯 BM25 下 MISS：heading 与正文拼成一条 token 流后，长正文稀释了标题信号。heading 词项重复计入后（同一语料（当时 232 chunk）复测）：R@1 0.480 → **0.520**，R@5 0.660 → **0.720**，R@10 0.760 → **0.800**。

### 2. 启发式重排在当前条件下零收益，且首版实现有负收益

首版重排只用「覆盖率 + 长度」，**把 RRF 融合分丢了**，R@1 从 0.52 掉到 0.34。修复（归一化融合分占 0.80 权重）后与 hybrid 打平（0.427 = 0.427）。结论：没有 cross-encoder 时，重排只增加约 2.5 倍延迟，不换取质量。

### 3. 低精度召回源会污染融合，不能按精度同等对待

图谱路扩表后召回覆盖 R@10 从 0.14 升到 0.44，但精度仅 0.060；同权全量参与使 hybrid R@1 从 0.52 跌到 0.36。限流（截断 12）+ 降权（0.35）能止损，但换不回收益。**本体路的正确角色是精确锚点，不是主力召回。**

### 4. 融合不优于单路，根因是「假向量」没有互补信息

见 §四。**上真实 embedding 是融合获得正收益的前置条件**，不是调权重能解决的。这条结论是本原型最重要的产出——它把「该不该做混合检索」从主观偏好变成了可复现的实验结果。

### 5. 剩余 MISS 主要是词汇不匹配，正好是需要语义检索的证据

| 查询 | 人工答案原文用词 | 结果 |
|---|---|---|
| Workflow 发布需要经过哪些**门禁** | 「进入 active 前**必须通过**…」 | MISS |
| 本体和知识图谱到底是什么**关系** | 「**核心分工**：Ontology=规范，KG=事实」 | MISS |

同义不同词，BM25 与 TF-IDF 都召回不到。这类失败指向查询扩展 / embedding，而非排序调参。

### 6. 评估集从 25 条扩到 62 条，结论方向不变但绝对值变了

25 条时 bm25 R@1 0.520，62 条时 0.548。扩样后 `architecture` 类（81 个候选）占比提高，暴露了「同类章节互相顶位」这个在 25 条上看不到的问题。**小评估集上 2pt 的差异不构成结论**，这是扩样的直接动机。距离 200 条仍需人工标注工时，`--suggest` 是为此提供的辅助工具。

## 六、错误码（`pipeline.ERROR_CODES`）

失败必须可归因。静默返回空列表是本项目最危险的失败模式——上层只看到「召回变差」，无法区分是术语表坏了、服务挂了还是语料没读到。

| 错误码 | 触发条件 | 处理方式 |
|---|---|---|
| `CORPUS_ROOT_INVALID` | 语料根目录不存在 | 硬失败 |
| `CORPUS_EMPTY` | 未发现任何可检索文件 / 全部被长度阈值过滤 | 硬失败 |
| `DOC_UNREADABLE` | 单文档读取失败（`strict=True`，默认） | 硬失败；`strict=False` 时告警跳过 |
| `ONTOLOGY_BRIDGE_INVALID` | 桥接资产缺字段 / id 重复 / 无出处 / schema 主版本不符 | 硬失败 |
| `ONTOLOGY_BRIDGE_AMBIGUOUS` | 同一表面形式归属多个概念 | 硬失败 |
| `ONTOLOGY_TERMS_EMPTY` | profile 术语抽取为空 | 硬失败 |
| `GOLDEN_INVALID` | 评估集缺字段 / qid 重复 / JSON 损坏 | 硬失败（不跳过坏行） |
| `GOLDEN_EMPTY` | 评估集为空 | 硬失败 |
| `GOLDEN_RELEVANT_UNRESOLVED` | 标注指向不存在的 chunk | 硬失败并列出 |
| `INVALID_MODE` | 检索模式不在 `MODES` 内 | 硬失败（v0.1 曾静默落入 rerank） |
| `INVALID_QUERY` | 查询非字符串 | 硬失败 |
| `INVALID_TOPK` | topk / depth 非法 | 硬失败 |
| `INVALID_FUSION` | 权重数 ≠ 召回路数 | 硬失败 |
| `EMBEDDING_UNAVAILABLE` | 向量服务未配置或不可达 | 硬失败（**不退回 TF-IDF**） |
| `EMBEDDING_HTTP_ERROR` | 非 2xx；4xx（除 429）不重试 | 硬失败 |
| `EMBEDDING_BAD_RESPONSE` | 响应不可解析 / 条数不符 / 含非数值 | 硬失败 |
| `EMBEDDING_DIM_MISMATCH` | 查询与索引向量维度不一致 | 硬失败 |

CLI 捕获 `RetrievalError` 后退出码为 2。

## 七、本体—检索桥接资产（`ontology_bridge.json`，schema 1.1）

图谱路的锚点来源，也是「本体对接」最直接的产出物。加载时强制执行：

- `schema_version` 主版本必须匹配（缺省时按 1.0 兼容并告警）；
- 每个概念必须有 `id`（唯一）、`surfaces`（非空）、`evidence`（文档出处，防编造）；
- `kind` 限定在 10 类词汇内（Entity / Contract / Metric / Tool / Process …）；
- **同一 surface 不得归属多个概念**——歧义会让实体链接失去确定锚点，直接硬失败。

当前 29 个概念、127 个表面形式，零冲突。`--validate` 会一并给出概念数与类别分布。

## 八、上生产要替换的三处

| 当前 | 生产 | 接口不变的部分 |
|---|---|---|
| `TfidfHashEmbedder` | bge-m3 / text-embedding-3 + Milvus/Qdrant | `Embedder.embed(text)` / `embed_many(texts)` |
| `GraphIndex` | Neo4j + 按 `CYPHER_EQUIVALENTS` 落地 | `search(query, topk, hops)` |
| 启发式 `rerank` | bge-reranker-v2-m3 | `rerank(query, candidates, by_id, topk)` |

注入方式已打通：`HybridRetriever(root, embedder=HttpEmbedder())`，检索代码零改动。`VectorIndex` 同时支持稠密（`list`）与稀疏（`dict`）向量。

另：语料需换成真实工单/文档库并接 ETL（清洗、去重、增量更新、失效删除）；评估集需扩到 200+ 条并做双人标注一致性校验。

## 九、三处必须记住的可复现性陷阱

1. **`hash()` 不能用**：Python 对 `str` 的内置 hash 加 per-process 随机盐，会让同一语料每次运行的哈希桶碰撞不同、指标漂移（实测 vector R@10 在 0.74/0.78 之间跳）。必须用 `zlib.crc32`。
2. **延迟指标不可比**：p50 只反映本机相对开销，跨机器/跨语言不可对比；比对指标时须剔除 `p50` 列，或直接比 `agg` 的其余字段。
3. **标注错了结论就废了**：复核 25 条标注时发现 q17 指向「Canonical 契约定义」段，而该段并不回答「版本绑定」。已更正。引用是否存在由 `--validate` 自动校验，**「是否真的回答了问题」只能人审**。

## 十、不能声称的内容

- 不能说「实现了生产级混合检索」——没有真实 embedding、没有向量库、没有真实数据。
- 不能说「图谱查询已落地」——没有 Neo4j，只有内存图与 Cypher 等价语义。
- 不能说「混合检索优于单路」——实测相反，见 §四。
- 不能把本文件的指标当作业务效果，它只衡量「本仓 233 个 chunk 上的检索质量」。
