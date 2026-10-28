# -*- coding: utf-8 -*-
"""pipeline.py — 混合检索原型 v0.2（分块 / BM25F / 向量路 / 图谱路 / RRF / 重排）

用途：验证「多路召回 + 融合 + 重排 + 可评估」这条管道在本仓语料上能真实跑通，
      并给出可复现的召回指标。不是生产检索服务。

── 边界声明（对外材料中不得省略）──────────────────────────────
1. 向量路默认用 TF-IDF（中文 char-bigram + 英文/数字词）哈希向量，
   **不是 embedding 模型**。接口按 Embedder 协议设计，生产替换
   bge-m3 / text-embedding-3 时检索代码不变，只是换 embedder 实现
   （已提供 HttpEmbedder，见 §Embedder）。
2. 图谱路用内存图（本体术语表 + 文档链接 + 章节相邻），**不是 Neo4j**。
   等价的 Cypher 语义见文件尾 `CYPHER_EQUIVALENTS`，接入时按该语义实现。
3. 重排用启发式（词项覆盖率 / 相邻短语 / 标题覆盖 / 融合分），
   **不是 cross-encoder**。生产替换 bge-reranker-v2-m3 时只需换 rerank() 实现。
4. 语料 = 本仓库 docs/ 下的真实文档，**不是真实工单/聊天记录库**。
   指标只证明「管道与评估可复现」，不证明生产检索质量。
5. 无第三方依赖（仅 Python 标准库），默认无网络访问（HttpEmbedder 需显式启用）。

── v0.2 相对 v0.1 的改动（检索语义未变，指标可跨版本对比）──────
- 新增统一错误类型 RetrievalError + 错误码表，替换裸异常与静默空结果；
- 新增语料指纹 corpus_fingerprint()，把「改文档即改基线」变成可比对的机制；
- 新增 HttpEmbedder（OpenAI/TEI 兼容）与 embed_many 批量接口，
  使「上真实 embedding」只需换一个注入对象，不需改检索代码；
- VectorIndex 同时支持稠密向量（list）与稀疏向量（dict）；
- Bridge 资产增加 schema 校验（必填字段 / id 唯一 / 跨概念表面形式冲突）；
- 修复：rerank 重复 tokenize 标题（缓存未复用）、未使用变量、未知 mode
  静默落入 rerank 分支、topk > depth 时静默截断。
"""
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import zlib
from collections import Counter, defaultdict

# ── 错误契约 ────────────────────────────────────────────────
# 错误码与 docs/interfaces/mcp-server-spec.md §5 的风格保持一致：
# 大写下划线、可直接映射为对外 API 的 error.code。
ERROR_CODES = {
    "CORPUS_ROOT_INVALID": "语料根目录不存在或不是目录",
    "CORPUS_EMPTY": "语料根目录下未发现任何可检索文件",
    "DOC_UNREADABLE": "单个文档读取失败（编码错误或权限不足）",
    "ONTOLOGY_BRIDGE_INVALID": "本体桥接资产结构不合规",
    "ONTOLOGY_BRIDGE_AMBIGUOUS": "同一表面形式归属多个概念，检索时无法确定锚点",
    "ONTOLOGY_TERMS_EMPTY": "本体术语抽取结果为空",
    "GOLDEN_INVALID": "评估集条目结构不合规（缺字段 / qid 重复 / JSON 损坏）",
    "GOLDEN_EMPTY": "评估集为空，无法计算指标",
    "GOLDEN_RELEVANT_UNRESOLVED": "评估集标注指向不存在的 chunk",
    "INVALID_MODE": "检索模式不在支持列表内",
    "INVALID_QUERY": "查询参数非法（非字符串 / 无法归一化）",
    "INVALID_TOPK": "topk / depth 参数非法",
    "INVALID_FUSION": "融合参数非法（权重与召回路数不匹配）",
    "EMBEDDING_UNAVAILABLE": "向量服务不可达或未配置",
    "EMBEDDING_HTTP_ERROR": "向量服务返回非 2xx",
    "EMBEDDING_BAD_RESPONSE": "向量服务响应结构不可解析",
    "EMBEDDING_DIM_MISMATCH": "查询向量与索引向量维度不一致",
}


class RetrievalError(Exception):
    """带错误码的显式失败。禁止用「返回空列表」掩盖配置或数据错误。

    设计理由：图谱路/向量路一旦静默返回空，上层看到的只是「召回变差」，
    很难定位到是术语表坏了还是服务挂了。错误码让失败可归因。
    """

    def __init__(self, code, detail="", **ctx):
        if code not in ERROR_CODES:
            raise ValueError("未登记的错误码: %s" % code)
        self.code = code
        self.detail = detail
        self.ctx = ctx
        msg = "[%s] %s" % (code, ERROR_CODES[code])
        if detail:
            msg += " — " + detail
        super().__init__(msg)


def warn(msg):
    """结构化告警走 stderr，绝不污染 stdout（stdout 留给指标表，也避免与 MCP 流冲突）。"""
    sys.stderr.write("[warn] %s\n" % msg)


# ── 语料范围 ────────────────────────────────────────────────
# architecture-polished.md 与 architecture.md 同源（v1.9 润色稿 vs v2.3 技术稿），
# 内容高度重叠。纳入会重复命中同一内容、虚抬召回率，故显式排除。
DOC_EXCLUDE = {"docs/architecture-polished.md"}
DOC_INCLUDE = (
    "docs/architecture.md",
    "docs/STATUS.md",
    "docs/interfaces",
    "docs/research",
    "docs/实施路径.txt",
    "docs/架构方案.txt",
)
TEXT_EXT = (".md", ".txt", ".yaml", ".yml")

MAX_CHARS = 900      # 单 chunk 正文上限（约 300–450 token 中文）
MIN_CHARS = 60       # 低于此长度的 section 视为标题残留，丢弃
OVERLAP = 150        # 超长 section 切分时的重叠字符数
MAX_QUERY_CHARS = 512  # 超长查询截断阈值（防御性；正常问句远低于此）

MODES = ("bm25", "vector", "graph", "hybrid", "hybrid_rerank")

# ── 分词 ────────────────────────────────────────────────────
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]*|\d+(?:\.\d+)*")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TOC = re.compile(r"^\s*\d+(\.\d+)*\.?\s*\[.+\]\(#.+\)\s*$")


def tokenize(text):
    """中文取 char-bigram（单字保留），英文/数字取小写词。不依赖分词器。"""
    if not text:
        return []
    text = text.lower()
    toks = []
    for m in _CJK.finditer(text):
        s = m.group(0)
        if len(s) == 1:
            toks.append(s)
        else:
            toks.extend(s[i:i + 2] for i in range(len(s) - 1))
    for m in _WORD.finditer(text):
        toks.append(m.group(0))
    return toks


def normalize_query(query):
    """查询归一化 + 长度守卫。纯空白/纯标点返回空串（调用方按「无结果」处理）。"""
    if not isinstance(query, str):
        raise RetrievalError("INVALID_QUERY", "query 必须是字符串，收到 %r" % type(query))
    q = query.strip()
    if len(q) > MAX_QUERY_CHARS:
        warn("查询长度 %d 超过 %d，已截断" % (len(q), MAX_QUERY_CHARS))
        q = q[:MAX_QUERY_CHARS]
    return q


def _slug(s):
    # 保留 ASCII 点，使章节号 "3.3" 不被吞成 "33"；中文句号仍去除
    s = re.sub(r"[`*_\[\]()（）:：,，。\\|>#\"'!?？、]+", "", s).strip()
    s = re.sub(r"\s+", "-", s)
    return s[:48] or "root"


# ── 分块 ────────────────────────────────────────────────────
def discover_docs(root):
    """按 DOC_INCLUDE 收集文件（确定性排序，保证 chunk_id 稳定）。

    缺个别配置项只告警（不同语料根可能没有 txt/目录）；
    整个语料根为空则抛 CORPUS_EMPTY——静默返回空语料会让评估得到 0 分却看不出原因。
    """
    if not os.path.isdir(root):
        raise RetrievalError("CORPUS_ROOT_INVALID", root)
    paths, missing = [], []
    for item in DOC_INCLUDE:
        p = os.path.join(root, item)
        if os.path.isfile(p):
            paths.append(p)
        elif os.path.isdir(p):
            for dirpath, _, files in os.walk(p):
                for f in files:
                    if f.lower().endswith(TEXT_EXT):
                        paths.append(os.path.join(dirpath, f))
        else:
            missing.append(item)
    if missing:
        warn("语料配置项不存在，已跳过: %s" % ", ".join(missing))
    out = []
    for p in sorted(set(paths)):
        rel = os.path.relpath(p, root).replace("\\", "/")
        if rel in DOC_EXCLUDE:
            continue
        out.append((rel, p))
    if not out:
        raise RetrievalError("CORPUS_EMPTY", "root=%s 命中 0 个文件" % root)
    return out


def _split_long(text, max_chars=MAX_CHARS, overlap=OVERLAP):
    """按段落聚合到 max_chars，切分时保留 overlap 字符的上下文。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = (buf + "\n\n" + p).strip()
            continue
        # buf 放不下下一段：只有够长才独立成 chunk，否则并入后续段，避免「方案分八层：」这类碎片
        if len(buf) >= MIN_CHARS:
            chunks.append(buf)
            buf = ""
        if len(p) <= max_chars:
            buf = ((buf + "\n\n" + p).strip() if buf else p)
        else:  # 单段超长：残余短 buf 并入开头，再硬切 + overlap
            if buf:
                p = buf + "\n" + p
                buf = ""
            step = max_chars - overlap
            for i in range(0, len(p), step):
                seg = p[i:i + max_chars]
                if len(seg) >= MIN_CHARS:
                    chunks.append(seg)
    if len(buf) >= MIN_CHARS:
        chunks.append(buf)
    return chunks


def _kind(rel_path):
    ext = os.path.splitext(rel_path)[1].lower()
    return {".md": "md", ".yaml": "yaml", ".yml": "yaml"}.get(ext, "plain")


_YAML_KEY = re.compile(r"^(\s{0,2})([A-Za-z_][\w\-]*):")


def _sections(rel_path, raw):
    """按文件类型切 section。YAML 不能按 markdown 的 # 切——
    它的注释行 `# 规则：…` 会被误判为一级标题，导致整文件塌成一块。"""
    kind = _kind(rel_path)
    if kind == "plain":
        return [(None, raw)]

    lines = raw.splitlines()
    sections, cur_head, cur_body, in_fence = [], None, [], False

    for ln in lines:
        if kind == "md":
            if _FENCE.match(ln):
                in_fence = not in_fence
                cur_body.append(ln)
                continue
            m = None if in_fence else _HEAD.match(ln)
            if m:
                if cur_head is not None or any(x.strip() for x in cur_body):
                    sections.append((cur_head, "\n".join(cur_body)))
                cur_head = m.group(2).strip()
                cur_body = []
                continue
            if _TOC.match(ln):
                continue
        else:  # yaml：只认缩进 <= 2 的顶层/二层 key
            m = _YAML_KEY.match(ln)
            if m:
                if cur_head is not None or any(x.strip() for x in cur_body):
                    sections.append((cur_head, "\n".join(cur_body)))
                cur_head = m.group(2)
                cur_body = []
                continue
        cur_body.append(ln)

    if cur_head is not None or any(x.strip() for x in cur_body):
        sections.append((cur_head, "\n".join(cur_body)))
    return sections


def chunk_document(rel_path, raw):
    """按 section 切分，再按长度二次切。返回 chunk 列表。"""
    doc_key = _slug(os.path.basename(rel_path).rsplit(".", 1)[0])
    chunks = []
    for head, body in _sections(rel_path, raw):
        body = body.strip()
        if len(body) < MIN_CHARS:
            continue
        title = head or "(untitled)"
        for i, part in enumerate(_split_long(body)):
            cid = "%s#%s#%d" % (doc_key, _slug(title), i)
            chunks.append({
                "chunk_id": cid,
                "doc": rel_path,
                "heading": title,
                "text": part,
            })
    return chunks


def build_chunks(root, strict=True):
    """构建 chunk 集。strict=True 时任一文档读失败即抛 DOC_UNREADABLE。

    默认 strict：评估流水线宁可中断也不要「少读了几个文件却照常出指标」。
    """
    chunks, failed = [], []
    for rel, path in discover_docs(root):
        try:
            with io.open(path, encoding="utf-8") as f:
                raw = f.read()
        except (OSError, UnicodeDecodeError) as e:
            failed.append((rel, "%s: %s" % (type(e).__name__, e)))
            if strict:
                raise RetrievalError("DOC_UNREADABLE", "%s (%s)" % (rel, e))
            continue
        chunks.extend(chunk_document(rel, raw))
    if failed:
        warn("跳过 %d 个不可读文档: %s" % (len(failed), "; ".join(r for r, _ in failed)))
    if not chunks:
        raise RetrievalError("CORPUS_EMPTY", "所有文档均未产出 chunk（检查 MIN_CHARS=%d）" % MIN_CHARS)

    # chunk_id 去重（同标题同序号理论上不冲突，防御性处理）
    seen = Counter()
    for c in chunks:
        n = seen[c["chunk_id"]]
        seen[c["chunk_id"]] += 1
        if n:
            c["chunk_id"] = "%s~%d" % (c["chunk_id"], n)
    for c in chunks:
        c["tokens"] = tokenize(c["heading"] + "\n" + c["text"])
        c["head_tokens"] = tokenize(c["heading"])
    return chunks


def corpus_fingerprint(chunks):
    """语料指纹：chunk_id + 正文的稳定摘要。

    语料包含本仓库文档，改一行文档就会改变 chunk 划分并推动指标基线。
    把指纹写进评估输出，才能判断「两次指标差异」是代码改动还是语料改动造成的。
    """
    h = hashlib.sha1()
    for c in sorted(chunks, key=lambda x: x["chunk_id"]):
        h.update(c["chunk_id"].encode("utf-8"))
        h.update(b"\x00")
        h.update(hashlib.sha1(c["text"].encode("utf-8")).hexdigest().encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()[:16]


# ── 本体桥接资产（规约 + 校验） ─────────────────────────────
# schema 1.1：concepts 的每项必须有 id / surfaces / evidence；
# kind 可选（缺省 Concept），用于区分实体、契约、指标、工具、方法等概念类型。
BRIDGE_SCHEMA_MAJOR = "1"
BRIDGE_KINDS = ("Concept", "Entity", "Relation", "Metric", "Contract",
                "Tool", "Process", "System", "Method", "Term")


def validate_bridge(doc, path="(inline)"):
    """校验桥接资产，返回归一化的 {canonical_id: [surface...]}。

    为什么不「容错跳过坏条目」：桥接表是图谱路的唯一锚点来源，一条坏条目
    会让某类查询永久召回不到，而这种缺失在指标上只表现为「召回率略低」，
    几乎无法归因。所以结构错误一律硬失败。
    """
    if not isinstance(doc, dict):
        raise RetrievalError("ONTOLOGY_BRIDGE_INVALID", "%s 顶层必须是对象" % path)
    if "schema_version" not in doc:
        warn("%s 缺少 schema_version，按 1.0 兼容模式加载（建议补 1.1）" % path)
    elif str(doc["schema_version"]).split(".")[0] != BRIDGE_SCHEMA_MAJOR:
        raise RetrievalError("ONTOLOGY_BRIDGE_INVALID",
                             "%s schema_version=%s 与支持的主版本 %s 不匹配"
                             % (path, doc["schema_version"], BRIDGE_SCHEMA_MAJOR))

    concepts = doc.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        raise RetrievalError("ONTOLOGY_BRIDGE_INVALID", "%s concepts 必须是非空数组" % path)

    terms, owner = {}, {}
    for i, c in enumerate(concepts):
        where = "%s concepts[%d]" % (path, i)
        if not isinstance(c, dict):
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID", "%s 不是对象" % where)
        cid = c.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID", "%s 缺少非空字符串 id" % where)
        if cid in terms:
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID", "%s id 重复: %s" % (where, cid))
        kind = c.get("kind", "Concept")
        if kind not in BRIDGE_KINDS:
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID",
                                 "%s kind=%r 不在允许集合内" % (where, kind))
        surfaces = c.get("surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID", "%s surfaces 必须是非空数组" % where)
        clean = []
        for s in surfaces:
            if not isinstance(s, str) or not s.strip():
                raise RetrievalError("ONTOLOGY_BRIDGE_INVALID",
                                     "%s surfaces 含空值或非字符串" % where)
            if s not in clean:
                clean.append(s)
        evidence = c.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            # evidence 是防编造的门：没有文档出处的概念不允许进桥接表
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID", "%s 缺少 evidence 出处" % where)
        terms[cid] = clean
        for s in clean:
            key = s.lower()
            if key in owner and owner[key] != cid:
                raise RetrievalError("ONTOLOGY_BRIDGE_AMBIGUOUS",
                                     "表面形式 %r 同时归属 %s 与 %s" % (s, owner[key], cid))
            owner[key] = cid
    return terms


def load_ontology_terms(profile_path, bridge_path=None):
    """加载术语表。优先级：显式桥接资产 > profile L2 正则解析。

    ontology_bridge.json 是「本体概念 → 检索表面形式」的显式映射，可审、可版本化；
    它比从 profile 里只抽 3 个制造术语覆盖更广，但仍要求每个 surface 有文档出处。
    解析为空一律报错，不静默降级为「图谱路永远返回空」——那种失败最难排查。
    """
    if bridge_path:
        if not os.path.isfile(bridge_path):
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID",
                                 "桥接文件不存在: %s" % bridge_path)
        try:
            with io.open(bridge_path, encoding="utf-8") as f:
                doc = json.loads(f.read())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise RetrievalError("ONTOLOGY_BRIDGE_INVALID",
                                 "%s 不可解析: %s" % (os.path.basename(bridge_path), e))
        return validate_bridge(doc, os.path.basename(bridge_path))

    if not os.path.isfile(profile_path):
        raise RetrievalError("ONTOLOGY_TERMS_EMPTY", "profile 不存在: %s" % profile_path)

    with io.open(profile_path, encoding="utf-8") as f:
        text = f.read()

    terms = {}
    for m in re.finditer(r"-\s*\{term:\s*([^,}]+?),\s*en:\s*([^,}]+?)[,}]", text):
        zh, en = m.group(1).strip(), m.group(2).strip()
        terms.setdefault(en, [en])
        if zh not in terms[en]:
            terms[en].append(zh)

    for m in re.finditer(r"-\s*\{type:\s*([^,}]+?),\s*aliases:\s*\[([^\]]*)\]", text):
        typ = m.group(1).strip()
        bucket = terms.setdefault(typ, [typ])
        for a in (x.strip() for x in m.group(2).split(",")):
            if a and a not in bucket:
                bucket.append(a)

    if not terms:
        raise RetrievalError("ONTOLOGY_TERMS_EMPTY",
                             "profile 术语抽取失败，检查 %s 格式" % os.path.basename(profile_path))
    return terms


# ── 索引 1：BM25（稀疏词项） ────────────────────────────────
class BM25Index:
    """BM25F 近似：heading 词项重复计入，模拟字段权重。

    不用纯 BM25 的原因（实测）：heading 与正文拼成一条 token 流后，
    长正文会稀释标题信号，导致「Workflow 发布需要经过哪些门禁」这类
    标题已含全部关键词的查询在 top-10 里查不到目标章节。
    """

    HEADING_BOOST = 2

    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tf = [Counter(c["tokens"] + c["head_tokens"] * self.HEADING_BOOST)
                   for c in chunks]
        self.dl = [len(c["tokens"]) + len(c["head_tokens"]) * self.HEADING_BOOST
                   for c in chunks]
        self.avgdl = sum(self.dl) / max(len(self.dl), 1)
        df = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, q_tokens, topk=50):
        if not q_tokens or topk <= 0:
            return []
        avgdl = self.avgdl or 1.0
        scores = []
        for i, tf in enumerate(self.tf):
            s, dl = 0.0, self.dl[i]
            for t in q_tokens:
                f = tf.get(t)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / avgdl))
            if s > 0:
                scores.append((s, i))
        scores.sort(key=lambda x: (-x[0], self.chunks[x[1]]["chunk_id"]))
        return [(self.chunks[i]["chunk_id"], s) for s, i in scores[:topk]]


# ── 索引 2：向量路 ──────────────────────────────────────────
class Embedder:
    """向量化协议。生产实现（bge-m3 / text-embedding-3）只需满足：

        embed(text) -> list[float] | dict[int, float]
        embed_many(texts) -> list[同上]      # 可选，默认逐个调用 embed

    返回 list 视为稠密向量，dict 视为稀疏向量；VectorIndex 两种都支持。
    """

    def embed(self, text):
        raise NotImplementedError

    def embed_many(self, texts):
        return [self.embed(t) for t in texts]


class TfidfHashEmbedder(Embedder):
    """零依赖替代实现：char-bigram TF-IDF → 16 位哈希稀疏向量 → L2 归一化。

    这不是语义向量：同义不同词（如「缺料」与「物料短缺」）召回不到。
    设计上刻意保留 Embedder 协议，就是为了让这个缺陷可被替换掉而不是被掩盖。

    哈希必须用 crc32 而非内置 hash()：后者对 str 加了 per-process 随机盐，
    会让同一份语料每次运行的桶碰撞不同、指标漂移，评估即失去可复现性。
    """

    DIM = 1 << 16

    def __init__(self, corpus_tokens):
        df = Counter()
        for toks in corpus_tokens:
            df.update(set(toks))
        n = max(len(corpus_tokens), 1)
        self.idf = {t: math.log((n + 1) / (d + 1)) + 1.0 for t, d in df.items()}
        self.default_idf = math.log(n + 1) + 1.0

    @staticmethod
    def _bucket(token):
        return zlib.crc32(token.encode("utf-8")) % TfidfHashEmbedder.DIM

    def embed(self, text):
        toks = tokenize(text)
        if not toks:
            return {}
        tf = Counter(toks)
        vec = defaultdict(float)
        for t, f in tf.items():
            w = (1 + math.log(f)) * self.idf.get(t, self.default_idf)
            vec[self._bucket(t)] += w
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {k: v / norm for k, v in vec.items()}


class HttpEmbedder(Embedder):
    """真实 embedding 服务的接入点（OpenAI /v1/embeddings 或 TEI 兼容）。

    配置优先级：显式参数 > 环境变量。全部缺失时构造即失败，
    不允许「悄悄退回 TF-IDF」——那会让人误以为用了语义检索。

        EMBEDDING_BASE_URL  如 http://127.0.0.1:8080
        EMBEDDING_MODEL     如 bge-m3
        EMBEDDING_API_KEY   可选
        EMBEDDING_PATH      可选，默认 /v1/embeddings
        EMBEDDING_DIM       可选，预期维度（给出则校验）

    状态：协议行为已用本地 stub 服务验证（见 test_retrieval.py）；**未接真实模型**。
    """

    def __init__(self, base_url=None, model=None, api_key=None, path=None,
                 timeout=10.0, retries=1, expect_dim=None, normalize=True,
                 batch_size=32):
        self.base_url = (base_url or os.environ.get("EMBEDDING_BASE_URL") or "").rstrip("/")
        if not self.base_url:
            raise RetrievalError("EMBEDDING_UNAVAILABLE",
                                 "未提供 base_url 且 EMBEDDING_BASE_URL 未设置")
        self.model = model or os.environ.get("EMBEDDING_MODEL") or "bge-m3"
        self.api_key = api_key if api_key is not None else os.environ.get("EMBEDDING_API_KEY")
        self.path = path or os.environ.get("EMBEDDING_PATH") or "/v1/embeddings"
        self.timeout = float(timeout)
        self.retries = max(int(retries), 0)
        self.normalize = normalize
        self.batch_size = max(int(batch_size), 1)
        env_dim = os.environ.get("EMBEDDING_DIM")
        self.dim = int(expect_dim or env_dim or 0) or None

    # -- 协议细节 --------------------------------------------------
    def _post(self, texts):
        import urllib.error
        import urllib.request

        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        url = self.base_url + self.path

        last = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                break
            except urllib.error.HTTPError as e:
                last = RetrievalError("EMBEDDING_HTTP_ERROR",
                                      "%s -> HTTP %d" % (url, e.code), status=e.code)
                # 4xx 除 429 外不重试：请求本身有问题，重试只会放大错误
                if 400 <= e.code < 500 and e.code != 429:
                    raise last
                if attempt == self.retries:
                    raise last
            except (urllib.error.URLError, OSError) as e:
                last = RetrievalError("EMBEDDING_UNAVAILABLE", "%s -> %s" % (url, e))
                if attempt == self.retries:
                    raise last
            time.sleep(0.2 * (attempt + 1))
        else:  # pragma: no cover - 循环必然 break 或 raise
            raise last

        try:
            doc = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise RetrievalError("EMBEDDING_BAD_RESPONSE", "响应非 JSON: %s" % e)

        vecs = None
        if isinstance(doc, list):
            # TEI / Infinity 风格：直接返回 [[...], ...]
            vecs = doc
        elif isinstance(doc, dict):
            if isinstance(doc.get("data"), list):
                try:
                    vecs = [d["embedding"] for d in doc["data"]]
                except (KeyError, TypeError):
                    raise RetrievalError("EMBEDDING_BAD_RESPONSE", "data[].embedding 缺失")
            elif isinstance(doc.get("embeddings"), list):
                vecs = doc["embeddings"]
            elif isinstance(doc.get("embedding"), list):
                vecs = [doc["embedding"]]
        if not isinstance(vecs, list) or not vecs:
            raise RetrievalError("EMBEDDING_BAD_RESPONSE",
                                 "未找到 data[].embedding / embeddings 字段")

        if len(vecs) != len(texts):
            raise RetrievalError("EMBEDDING_BAD_RESPONSE",
                                 "请求 %d 条、返回 %d 条" % (len(texts), len(vecs)))
        out = []
        for v in vecs:
            if not isinstance(v, (list, tuple)) or not v:
                raise RetrievalError("EMBEDDING_BAD_RESPONSE", "向量不是非空数组")
            try:
                v = [float(x) for x in v]
            except (TypeError, ValueError):
                raise RetrievalError("EMBEDDING_BAD_RESPONSE", "向量含非数值元素")
            if self.dim is None:
                self.dim = len(v)
            elif len(v) != self.dim:
                raise RetrievalError("EMBEDDING_DIM_MISMATCH",
                                     "期望 %d 维，实际 %d 维" % (self.dim, len(v)))
            if self.normalize:
                norm = math.sqrt(sum(x * x for x in v)) or 1.0
                v = [x / norm for x in v]
            out.append(v)
        return out

    def embed(self, text):
        return self.embed_many([text])[0]

    def embed_many(self, texts):
        if not texts:
            return []
        out = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._post(list(texts[i:i + self.batch_size])))
        return out


class VectorIndex:
    """向量索引。同时支持稠密（list）与稀疏（dict）向量，cosine = 归一化点积。"""

    def __init__(self, chunks, embedder):
        self.chunks = chunks
        self.embedder = embedder
        self.vecs = embedder.embed_many([c["heading"] + "\n" + c["text"] for c in chunks])
        if len(self.vecs) != len(chunks):
            raise RetrievalError("EMBEDDING_BAD_RESPONSE",
                                 "向量数 %d 与 chunk 数 %d 不一致" % (len(self.vecs), len(chunks)))
        self.dense = bool(self.vecs) and isinstance(self.vecs[0], (list, tuple))
        self.dim = None
        if self.dense:
            dims = {len(v) for v in self.vecs}
            if len(dims) > 1:
                raise RetrievalError("EMBEDDING_DIM_MISMATCH",
                                     "索引向量维度不一致: %s" % sorted(dims))
            self.dim = dims.pop() if dims else None

    def _score(self, qv, dv):
        if self.dense:
            return sum(a * b for a, b in zip(qv, dv))
        return sum(w * dv.get(k, 0.0) for k, w in qv.items())

    def search(self, query, topk=50):
        if topk <= 0:
            return []
        qv = self.embedder.embed(query)
        if not qv:
            return []
        if self.dense and self.dim is not None and len(qv) != self.dim:
            raise RetrievalError("EMBEDDING_DIM_MISMATCH",
                                 "查询 %d 维，索引 %d 维" % (len(qv), self.dim))
        scores = []
        for i, dv in enumerate(self.vecs):
            s = self._score(qv, dv)
            if s > 0:
                scores.append((s, i))
        scores.sort(key=lambda x: (-x[0], self.chunks[x[1]]["chunk_id"]))
        return [(self.chunks[i]["chunk_id"], s) for s, i in scores[:topk]]


# ── 索引 3：图谱路（本体术语 + 文档链接 + 章节相邻） ────────
class GraphIndex:
    """内存三元组图。节点类型：Term / Chunk / Doc。边：
         Term  -[:DEFINED_IN]-> Chunk      （术语出现在哪些 chunk）
         Term  -[:ALIAS_OF]->   Term
         Chunk -[:NEXT]->       Chunk      （同文档相邻章节）
         Chunk -[:LINKS_TO]->   Doc        （markdown 内链指向）
    查询时做实体链接：query 里命中的术语 → 关联 chunk，按术语长度加权。
    """

    HOP_DECAY = 0.25

    def __init__(self, chunks, terms):
        self.chunks = chunks
        self.by_id = {c["chunk_id"]: c for c in chunks}
        self.terms = terms                      # {canonical: [surface...]}
        # 预先把表面形式小写化：构建期是 O(chunk × concept × surface)，
        # 每条都重新 .lower() 在语料上万后会成为构建瓶颈。
        self._lower = {k: [s.lower() for s in v] for k, v in terms.items()}
        self.term_chunks = defaultdict(set)
        for c in chunks:
            low = (c["heading"] + "\n" + c["text"]).lower()
            for canon, surfaces in self._lower.items():
                if any(s in low for s in surfaces):
                    self.term_chunks[canon].add(c["chunk_id"])
        self.next_hop = defaultdict(set)
        bydoc = defaultdict(list)
        for c in chunks:
            bydoc[c["doc"]].append(c)
        for doc, lst in bydoc.items():
            for a, b in zip(lst, lst[1:]):
                self.next_hop[a["chunk_id"]].add(b["chunk_id"])
                self.next_hop[b["chunk_id"]].add(a["chunk_id"])
        self.link_targets = defaultdict(set)
        for c in chunks:
            for m in re.finditer(r"\[[^\]]+\]\(([^)#:]+)", c["text"]):
                self.link_targets[c["chunk_id"]].add(_slug(
                    os.path.basename(m.group(1)).rsplit(".", 1)[0]))

    def search(self, query, topk=50, hops=1):
        if not query or topk <= 0:
            return []
        qlow = query.lower()
        hit_terms = []
        for canon, surfaces in self._lower.items():
            matched = [len(s) for s in surfaces if s in qlow]
            if matched:
                # 取最长匹配的表面形式：越具体（越长）的表述，语义约束越强
                hit_terms.append((canon, max(matched)))
        if not hit_terms:
            return []
        scores = defaultdict(float)
        for canon, weight in hit_terms:
            for cid in self.term_chunks.get(canon, ()):
                scores[cid] += weight          # 长术语（更具体）权重更高
        if hops >= 1:                          # 1 跳扩展，权重衰减
            for cid, s in list(scores.items()):
                for nb in self.next_hop.get(cid, ()):
                    scores[nb] += s * self.HOP_DECAY
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:topk]

    def explain(self, query):
        """返回命中的术语，用于可解释性与调试。"""
        qlow = (query or "").lower()
        return [canon for canon, surfaces in self._lower.items()
                if any(s in qlow for s in surfaces)]


# ── 融合：RRF ───────────────────────────────────────────────
def rrf_fuse(rankings, k=60, weights=None, topk=50):
    """Reciprocal Rank Fusion。只用名次，不用各路分数量纲，避免归一化偏差。"""
    if not rankings:
        return []
    weights = weights or [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise RetrievalError("INVALID_FUSION",
                             "weights 长度 %d 与 rankings 路数 %d 不一致"
                             % (len(weights), len(rankings)))
    acc = defaultdict(float)
    for w, ranking in zip(weights, rankings):
        for rank, (cid, _) in enumerate(ranking, start=1):
            acc[cid] += w / (k + rank)
    ranked = sorted(acc.items(), key=lambda x: (-x[1], x[0]))
    return ranked[:topk]


# ── 重排（启发式；生产替换 cross-encoder） ──────────────────
def rerank(query, candidates, by_id, topk=10):
    """启发式重排：融合分为底 + 词项覆盖 + 相邻短语 + 标题命中。

    注意（踩过的坑）：早期版本只用「覆盖率 + 长度」重排，把 RRF 融合分丢了，
    实测 R@1 从 0.52 掉到 0.34——重排阶段必须保留召回阶段的分数量级信息，
    否则等于用更弱的信号覆盖更强信号。此处 base 归一化后占主导权重。
    """
    if not candidates or topk <= 0:
        return []
    q_toks = tokenize(query)
    q_set = set(q_toks)
    q_bi = set(zip(q_toks, q_toks[1:]))
    max_base = max((b for _, b in candidates), default=1.0) or 1.0
    scored = []
    for cid, base in candidates:
        c = by_id[cid]
        toks = c["tokens"]
        head_toks = c["head_tokens"]           # v0.2：复用建库缓存，不再重复 tokenize
        cov = len(q_set & set(toks)) / max(len(q_set), 1)
        prox = len(q_bi & set(zip(toks, toks[1:]))) / max(len(q_bi), 1)
        head_cov = len(q_set & set(head_toks)) / max(len(q_set), 1)
        score = (0.80 * (base / max_base) + 0.12 * cov
                 + 0.05 * prox + 0.03 * head_cov)
        scored.append((score, cid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(cid, s) for s, cid in scored[:topk]]


# ── 编排 ────────────────────────────────────────────────────
class HybridRetriever:
    """混合检索器。embedder 可注入，默认零依赖 TF-IDF 替身。

        HybridRetriever(root)                       # 零依赖，可离线
        HybridRetriever(root, embedder=HttpEmbedder())   # 真实 embedding
    """

    def __init__(self, root, embedder=None, bridge_path=None, profile_path=None,
                 strict=True):
        self.chunks = build_chunks(root, strict=strict)
        self.by_id = {c["chunk_id"]: c for c in self.chunks}
        self.fingerprint = corpus_fingerprint(self.chunks)
        self.bm25 = BM25Index(self.chunks)
        self.embedder = embedder or TfidfHashEmbedder([c["tokens"] for c in self.chunks])
        self.vector = VectorIndex(self.chunks, self.embedder)
        prof = profile_path or os.path.join(
            root, "docs", "interfaces", "profile-manufacturing-equipment-fault-v1.yaml")
        bridge = bridge_path if bridge_path is not None else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ontology_bridge.json")
        self.terms = load_ontology_terms(prof, bridge or None)
        self.graph = GraphIndex(self.chunks, self.terms)

    # -- 内部：三路召回 -------------------------------------------
    def _bm25_hits(self, query, depth):
        return self.bm25.search(tokenize(query), depth)

    def _vector_hits(self, query, depth):
        return self.vector.search(query, depth)

    def _graph_hits(self, query, limit=12):
        return self.graph.search(query, limit)

    def retrieve(self, query, mode="hybrid", topk=10, depth=50):
        """mode: bm25 | vector | graph | hybrid | hybrid_rerank

        返回 [(chunk_id, score), ...]，长度 <= topk。
        """
        if mode not in MODES:
            raise RetrievalError("INVALID_MODE", "%r 不在 %s 内" % (mode, list(MODES)))
        if not isinstance(topk, int) or topk < 0 or not isinstance(depth, int):
            raise RetrievalError("INVALID_TOPK", "topk=%r depth=%r" % (topk, depth))
        if topk == 0:
            return []
        depth = max(depth, topk)               # v0.2：不再静默截断到 depth
        q = normalize_query(query)
        if not q:
            return []

        if mode == "bm25":
            return self._bm25_hits(q, depth)[:topk]
        if mode == "vector":
            return self._vector_hits(q, depth)[:topk]
        if mode == "graph":
            return self._graph_hits(q, depth)[:topk]

        # 图谱路只作「精确锚点」：限定候选数并降权。
        # 实测若与 BM25/向量同权全量参与，其低精度候选会污染融合，
        # hybrid 的 R@1 从 0.52 掉到 0.36——低精度召回源不能按高精度源同等对待。
        fused = rrf_fuse([self._bm25_hits(q, depth),
                          self._vector_hits(q, depth),
                          self._graph_hits(q)],
                         weights=[1.0, 1.0, 0.35], topk=depth)
        if mode == "hybrid":
            return fused[:topk]
        return rerank(q, fused, self.by_id, topk)

    def explain(self, query):
        """调试入口：返回各路命中数与图谱链接到的术语。"""
        q = normalize_query(query)
        if not q:
            return {"query": q, "terms": [], "bm25": 0, "vector": 0, "graph": 0}
        return {
            "query": q,
            "terms": self.graph.explain(q),
            "bm25": len(self._bm25_hits(q, 50)),
            "vector": len(self._vector_hits(q, 50)),
            "graph": len(self._graph_hits(q)),
        }


# ── 生产接入时需实现的等价查询（供 Neo4j 落地参考，非本原型执行） ──
CYPHER_EQUIVALENTS = {
    "term_lookup": """
        // 实体链接：查询文本命中的本体术语 → 定义/出现该术语的 chunk
        MATCH (t:Term)-[:ALIAS_OF*0..1]->(c:Term)-[:DEFINED_IN]->(ch:Chunk)
        WHERE any(s IN c.surfaces WHERE toLower($query) CONTAINS toLower(s))
        RETURN ch.chunk_id AS chunk_id, size(collect(c.surfaces)) AS term_weight
        ORDER BY term_weight DESC LIMIT $k
    """,
    "one_hop_expand": """
        // 1 跳扩展：命中 chunk 的相邻章节（权重衰减 0.25）
        MATCH (seed:Chunk)-[:NEXT]-(nb:Chunk)
        WHERE seed.chunk_id IN $seeds
        RETURN nb.chunk_id AS chunk_id LIMIT $k
    """,
    "fusion_rrf": """
        // RRF 融合：三路名次合并（Cypher 侧需在应用层聚合，或用
        // apoc.coll.zip 后 reduce；此处仅表达语义）
        // score = Σ w_i / (60 + rank_i)
    """,
    "bridge_asset": """
        // 桥接资产的图化：概念作为稳定锚点，表面形式只作为入口属性
        // （若把 surface 建成节点，同义词会变成实体，语义立刻失准）
        MERGE (c:Concept {id: $concept_id})
        SET c.kind = $kind, c.evidence = $evidence, c.surfaces = $surfaces
    """,
}
