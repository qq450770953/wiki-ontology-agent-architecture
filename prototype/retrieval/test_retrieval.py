# -*- coding: utf-8 -*-
"""test_retrieval.py — 检索管道的单元测试与边界测试（零第三方依赖）

运行：
    python -m unittest discover -s prototype/retrieval -v
    python prototype/retrieval/test_retrieval.py

设计原则：
1. **不依赖仓库真实内容**：分块、索引、指标、错误路径的断言全部基于临时语料，
   仓库文档一改就让测试变红，等于把「改文档即改基线」的问题搬进 CI。
2. **断言行为而非数字**：指标只对手算得出的小样本做精确断言；
   真实语料只做冒烟测试（能跑、chunk 数 > 0、指标在 [0,1]）。
3. **错误路径必须有用例**：静默返回空是本项目最危险的失败模式，
   每条 RetrievalError 分支都应有对应用例。
"""
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evaluate as E
import pipeline as P


# ── 测试夹具 ────────────────────────────────────────────────
def _chunk(cid, heading, text):
    """构造一个与 build_chunks 输出同构的 chunk。"""
    return {"chunk_id": cid, "doc": "docs/x.md", "heading": heading, "text": text,
            "tokens": P.tokenize(heading + "\n" + text),
            "head_tokens": P.tokenize(heading)}


class Corpus:
    """临时语料：按 docs/ 结构落盘，用完即删。

    正文长度必须超过 pipeline.MIN_CHARS，否则 section 会被当成标题残留丢弃——
    夹具太短会让测试测的是「过滤逻辑」而不是「目标行为」。
    """

    FILES = {
        "docs/architecture.md": (
            "# 总览\n\n"
            "## 3.3 任务控制契约\n\n"
            "accepted 不是成功。动作状态为 unknown 时必须回查外部业务状态，禁止盲目重试。\n"
            "完成证据必须来自外部回执或状态回读，模型汇总只能作为解释性 Artifact。\n"
            "这条规则的目的是防止把「接口已受理」误当成「业务已完成」。\n\n"
            "## 6.4 SQL 模板\n\n"
            "LLM 只填参数，不得生成 SQL。模板需登记 required_permission 与口径版本。\n"
            "参数经 Ontology 实体白名单校验后才能进入执行阶段，越权参数直接拒绝。\n"
            "模板版本变化时必须同步更新 mapping_ref，禁止使用 latest 这类浮动引用。\n"
        ),
        "docs/research/many.md": "".join(
            "## 章节%d\n\n回查外部状态是必须的步骤，任何未知结果都不能靠模型自述来终结。\n"
            "本节讨论第 %d 类场景下的回查方式、超时处理与幂等键设计要点。\n\n" % (i, i)
            for i in range(1, 7)),
        "docs/interfaces/profile-x.yaml": (
            "# 这是 YAML 注释，不是 markdown 标题\n"
            "layers:\n"
            "  L2_domain:\n"
            "    term_definitions:\n"
            "      - {term: 故障码, en: FaultCode}\n"
            "      - {term: 工位, en: Station}\n"
            "    aliases:\n"
            "      - {type: FaultCode, aliases: [错误码, alarm code]}\n"
        ),
    }

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="retr_test_")
        for rel, body in self.FILES.items():
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(body)

    def path(self, rel):
        return os.path.join(self.root, rel)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _write_bridge(path, concepts, schema_version="1.1"):
    doc = {"concepts": concepts}
    if schema_version:
        doc["schema_version"] = schema_version
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False))
    return path


# ── 分词与分块 ──────────────────────────────────────────────
class TestTokenize(unittest.TestCase):
    def test_cjk_bigram_and_single_char(self):
        self.assertEqual(P.tokenize("门禁"), ["门禁"])
        self.assertEqual(P.tokenize("门"), ["门"])
        self.assertEqual(P.tokenize("发布门禁"), ["发布", "布门", "门禁"])

    def test_ascii_and_number_lowercased(self):
        self.assertIn("taskrun", P.tokenize("TaskRun"))
        self.assertIn("3.3", P.tokenize("版本 3.3"))

    def test_empty_and_punctuation_only(self):
        self.assertEqual(P.tokenize(""), [])
        self.assertEqual(P.tokenize("，。？！"), [])

    def test_slug_keeps_section_number(self):
        # v0.1 曾把 "3.3" 吞成 "33"，导致 chunk_id 无法定位章节
        self.assertIn("3.3", P._slug("3.3 任务控制契约"))
        self.assertNotIn("，", P._slug("a，b"))
        self.assertEqual(P._slug(""), "root")


class TestChunking(unittest.TestCase):
    def setUp(self):
        self.c = Corpus()

    def tearDown(self):
        self.c.cleanup()

    def test_chunk_ids_unique_and_stable(self):
        a = [x["chunk_id"] for x in P.build_chunks(self.c.root)]
        b = [x["chunk_id"] for x in P.build_chunks(self.c.root)]
        self.assertEqual(a, b, "同一语料两次构建的 chunk_id 必须一致")
        self.assertEqual(len(a), len(set(a)), "chunk_id 必须唯一")

    def test_markdown_heading_split(self):
        chunks = P.chunk_document("docs/architecture.md",
                                  Corpus.FILES["docs/architecture.md"])
        heads = [x["heading"] for x in chunks]
        self.assertIn("3.3 任务控制契约", heads)
        self.assertIn("6.4 SQL 模板", heads)

    def test_yaml_comment_not_treated_as_heading(self):
        """YAML 里的 `# 这是注释` 曾被当成一级标题，导致整文件塌成一块。

        注意：本实现只把缩进 <= 2 的 key 当标题，`    term_definitions:`（4 空格）
        属于 L2_domain 的正文，这是有意为之——不切到叶子级，避免产出大量碎块。
        """
        raw = Corpus.FILES["docs/interfaces/profile-x.yaml"]
        chunks = P.chunk_document("docs/interfaces/profile-x.yaml", raw)
        heads = [x["heading"] for x in chunks]
        self.assertNotIn("这是 YAML 注释，不是 markdown 标题", heads)
        self.assertIn("L2_domain", heads)
        self.assertTrue(any("term_definitions" in c["text"] for c in chunks),
                        "缩进更深的 key 应留在正文里")

    def test_fenced_code_block_heading_ignored(self):
        body = "正文内容需要足够长才能成为一个 chunk。" * 5
        raw = ("## 真标题\n\n" + body + "\n\n```\n## 这是代码里的井号\n```\n\n" + body)
        chunks = P.chunk_document("docs/x.md", raw)
        self.assertEqual([c["heading"] for c in chunks], ["真标题"])
        self.assertIn("这是代码里的井号", chunks[0]["text"])

    def test_no_fragment_chunks(self):
        """短段落不能被单独切成碎片 chunk（曾出现 6 字符的「方案分八层：」）。"""
        body = "方案分八层：\n\n" + "这是一个足够长的段落。" * 30
        chunks = P.chunk_document("docs/x.md", "## 标题\n\n" + body)
        self.assertTrue(chunks)
        for c in chunks:
            self.assertGreaterEqual(len(c["text"]), P.MIN_CHARS)

    def test_short_section_dropped(self):
        """低于 MIN_CHARS 的 section 必须被丢弃，而不是被当成独立 chunk 进索引。"""
        chunks = P.chunk_document("docs/x.md", "## 标题\n\n太短了。\n")
        self.assertEqual(chunks, [])

    def test_overlong_paragraph_hard_split_with_overlap(self):
        para = "甲" * (P.MAX_CHARS * 2 + 100)
        chunks = P.chunk_document("docs/x.md", "## 标题\n\n" + para)
        self.assertGreaterEqual(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c["text"]), P.MAX_CHARS)


class TestFingerprint(unittest.TestCase):
    def test_fingerprint_stable_and_content_sensitive(self):
        c = Corpus()
        try:
            fp1 = P.corpus_fingerprint(P.build_chunks(c.root))
            fp2 = P.corpus_fingerprint(P.build_chunks(c.root))
            self.assertEqual(fp1, fp2, "同一语料指纹必须稳定，否则无法判断基线漂移")
            with io.open(c.path("docs/architecture.md"), "a", encoding="utf-8") as f:
                f.write("\n追加一句话改变语料。\n")
            self.assertNotEqual(fp1, P.corpus_fingerprint(P.build_chunks(c.root)))
        finally:
            c.cleanup()


# ── 索引 ────────────────────────────────────────────────────
class TestBM25(unittest.TestCase):
    def test_heading_boost_ranks_heading_match_first(self):
        """查询词全在标题里时，标题命中的 chunk 必须排第一（BM25F 的核心收益）。"""
        head_hit = _chunk("a#发布门禁#0", "6. 发布门禁", "本节说明上线前需要经过的检查项与责任人。")
        body_hit = _chunk("b#其他#0", "其他章节",
                          "无关内容" * 30 + " 门禁 发布 " + "无关内容" * 30)
        q = P.tokenize("发布门禁")
        idx = P.BM25Index([head_hit, body_hit])
        ranked = [cid for cid, _ in idx.search(q)]
        self.assertEqual(ranked[0], "a#发布门禁#0")

    def test_empty_query_and_zero_topk(self):
        idx = P.BM25Index([_chunk("a#t#0", "t", "正文内容")])
        self.assertEqual(idx.search([], 10), [])
        self.assertEqual(idx.search(P.tokenize("正文"), 0), [])

    def test_no_match_returns_empty(self):
        idx = P.BM25Index([_chunk("a#t#0", "t", "正文内容")])
        self.assertEqual(idx.search(P.tokenize("完全不相干的词汇"), 10), [])


class TestTFIDFEmbedder(unittest.TestCase):
    def test_deterministic_across_calls(self):
        """内置 hash() 有 per-process 随机盐，会导致指标每次运行漂移；必须用 crc32。"""
        emb = P.TfidfHashEmbedder([P.tokenize("门禁 发布 契约")])
        v1, v2 = emb.embed("门禁 发布"), emb.embed("门禁 发布")
        self.assertEqual(v1, v2)

    def test_norm_is_one(self):
        emb = P.TfidfHashEmbedder([P.tokenize("门禁 发布 契约")])
        v = emb.embed("门禁 发布 契约")
        self.assertAlmostEqual(sum(x * x for x in v.values()), 1.0, places=6)

    def test_empty_text_gives_empty_vector(self):
        emb = P.TfidfHashEmbedder([P.tokenize("正文")])
        self.assertEqual(emb.embed("，。"), {})


class TestVectorIndexDense(unittest.TestCase):
    class StubDense(P.Embedder):
        """模拟真实 embedding：稠密 list。"""

        DIM = 4

        def embed(self, text):
            v = [0.0] * self.DIM
            v[0] = 1.0 if "门禁" in text else 0.0
            v[1] = 1.0 if "契约" in text else 0.0
            return v

    def test_dense_vectors_supported(self):
        chunks = [_chunk("a#门禁#0", "门禁", "发布前必须过门禁"),
                  _chunk("b#契约#0", "契约", "定义动作状态")]
        idx = P.VectorIndex(chunks, self.StubDense())
        self.assertTrue(idx.dense)
        self.assertEqual([c for c, _ in idx.search("门禁")][0], "a#门禁#0")

    def test_dim_mismatch_raises(self):
        class Bad(self.StubDense):
            def embed(self, text):
                return [1.0, 0.0] if "门禁" in text else [1.0, 0.0, 0.0]
        chunks = [_chunk("a#x#0", "x", "门禁"), _chunk("b#y#0", "y", "其他")]
        with self.assertRaises(P.RetrievalError) as cm:
            P.VectorIndex(chunks, Bad())
        self.assertEqual(cm.exception.code, "EMBEDDING_DIM_MISMATCH")


class TestGraphIndex(unittest.TestCase):
    def test_no_term_hit_returns_empty(self):
        chunks = [_chunk("a#x#0", "x", "无关正文")]
        gi = P.GraphIndex(chunks, {"FaultCode": ["故障码"]})
        self.assertEqual(gi.search("完全不相干的问题"), [])

    def test_term_links_to_chunk_and_hop_expand(self):
        chunks = [_chunk("a#故障码#0", "故障码", "E102 表示伺服过载，需要停机检查。"),
                  _chunk("a#处置#0", "处置步骤", "断电后复位驱动器，再空跑验证。")]
        gi = P.GraphIndex(chunks, {"FaultCode": ["故障码"]})
        hits = [cid for cid, _ in gi.search("故障码是什么意思")]
        self.assertIn("a#故障码#0", hits)
        self.assertIn("a#处置#0", hits, "1 跳扩展应带出相邻章节")
        self.assertLess(dict(gi.search("故障码")).get("a#处置#0", 0),
                        dict(gi.search("故障码")).get("a#故障码#0", 0),
                        "1 跳扩展权重必须衰减")

    def test_explain_lists_matched_terms(self):
        chunks = [_chunk("a#x#0", "x", "故障码 定义")]
        gi = P.GraphIndex(chunks, {"FaultCode": ["故障码"]})
        self.assertEqual(gi.explain("故障码怎么查"), ["FaultCode"])
        self.assertEqual(gi.explain(""), [])
        self.assertEqual(gi.explain(None), [])


# ── 融合与重排 ──────────────────────────────────────────────
class TestFusion(unittest.TestCase):
    def test_rrf_uses_rank_only(self):
        r1 = [("a", 99.0), ("b", 1.0)]
        r2 = [("b", 0.1)]
        fused = [cid for cid, _ in P.rrf_fuse([r1, r2])]
        # b: 1/62 + 1/61 > a: 1/61 —— 名次贡献，与原始分数量纲无关
        self.assertEqual(fused[0], "b")

    def test_rrf_empty_and_weight_mismatch(self):
        self.assertEqual(P.rrf_fuse([]), [])
        with self.assertRaises(P.RetrievalError) as cm:
            P.rrf_fuse([[("a", 1.0)]], weights=[1.0, 1.0])
        self.assertEqual(cm.exception.code, "INVALID_FUSION")

    def test_rerank_keeps_fused_score_dominant(self):
        """v0.1 首版重排丢弃融合分，R@1 从 0.52 掉到 0.34——此用例防回归。"""
        by_id = {c["chunk_id"]: c for c in [_chunk("a#x#0", "x", "完全无关"),
                                            _chunk("b#y#0", "y", "完全无关")]}
        ranked = [cid for cid, _ in P.rerank("门禁", [("a#x#0", 10.0), ("b#y#0", 1.0)], by_id)]
        self.assertEqual(ranked, ["a#x#0", "b#y#0"])

    def test_rerank_edge_cases(self):
        self.assertEqual(P.rerank("q", [], {}), [])
        self.assertEqual(P.rerank("q", [("a", 1.0)], {}, topk=0), [])


# ── 桥接资产校验 ────────────────────────────────────────────
class TestBridgeValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bridge_")
        self.profile = os.path.join(self.tmp, "profile.yaml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, concepts, schema_version="1.1"):
        p = _write_bridge(os.path.join(self.tmp, "b.json"), concepts, schema_version)
        return P.load_ontology_terms(self.profile, p)

    def test_valid_bridge(self):
        terms = self._load([{"id": "A", "kind": "Entity", "surfaces": ["甲", "甲甲"],
                             "evidence": "docs/x.md"}])
        self.assertEqual(terms, {"A": ["甲", "甲甲"]})

    def test_missing_evidence_rejected(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._load([{"id": "A", "surfaces": ["甲"]}])
        self.assertEqual(cm.exception.code, "ONTOLOGY_BRIDGE_INVALID")

    def test_empty_surfaces_rejected(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._load([{"id": "A", "surfaces": [], "evidence": "x"}])
        self.assertEqual(cm.exception.code, "ONTOLOGY_BRIDGE_INVALID")

    def test_duplicate_id_rejected(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._load([{"id": "A", "surfaces": ["甲"], "evidence": "x"},
                        {"id": "A", "surfaces": ["乙"], "evidence": "x"}])
        self.assertEqual(cm.exception.code, "ONTOLOGY_BRIDGE_INVALID")

    def test_ambiguous_surface_rejected(self):
        """同一表面形式归属两个概念会让实体链接失去确定锚点，必须硬失败。"""
        with self.assertRaises(P.RetrievalError) as cm:
            self._load([{"id": "A", "surfaces": ["门禁"], "evidence": "x"},
                        {"id": "B", "surfaces": ["门禁"], "evidence": "y"}])
        self.assertEqual(cm.exception.code, "ONTOLOGY_BRIDGE_AMBIGUOUS")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._load([{"id": "A", "kind": "Nonsense", "surfaces": ["甲"], "evidence": "x"}])
        self.assertEqual(cm.exception.code, "ONTOLOGY_BRIDGE_INVALID")

    def test_schema_major_mismatch_rejected(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._load([{"id": "A", "surfaces": ["甲"], "evidence": "x"}], schema_version="2.0")
        self.assertEqual(cm.exception.code, "ONTOLOGY_BRIDGE_INVALID")

    def test_missing_file_rejected(self):
        with self.assertRaises(P.RetrievalError) as cm:
            P.load_ontology_terms(self.profile, os.path.join(self.tmp, "nope.json"))
        self.assertEqual(cm.exception.code, "ONTOLOGY_BRIDGE_INVALID")

    def test_profile_fallback_regex(self):
        with io.open(self.profile, "w", encoding="utf-8") as f:
            f.write(Corpus.FILES["docs/interfaces/profile-x.yaml"])
        terms = P.load_ontology_terms(self.profile, None)
        self.assertIn("FaultCode", terms)
        self.assertIn("故障码", terms["FaultCode"])
        self.assertIn("错误码", terms["FaultCode"])

    def test_profile_without_terms_rejected(self):
        with io.open(self.profile, "w", encoding="utf-8") as f:
            f.write("layers:\n  L2_domain: {}\n")
        with self.assertRaises(P.RetrievalError) as cm:
            P.load_ontology_terms(self.profile, None)
        self.assertEqual(cm.exception.code, "ONTOLOGY_TERMS_EMPTY")


# ── 检索编排的边界条件 ──────────────────────────────────────
class TestRetrieverEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = Corpus()
        cls.bridge = _write_bridge(os.path.join(cls.c.root, "bridge.json"), [
            {"id": "FaultCode", "kind": "Entity", "surfaces": ["故障码", "错误码"],
             "evidence": "profile L2"},
            {"id": "Action", "kind": "Contract", "surfaces": ["accepted", "unknown"],
             "evidence": "architecture §3.3"},
        ])
        cls.r = P.HybridRetriever(cls.c.root, bridge_path=cls.bridge, strict=True)

    @classmethod
    def tearDownClass(cls):
        cls.c.cleanup()

    def test_empty_query_returns_empty(self):
        for m in P.MODES:
            self.assertEqual(self.r.retrieve("", m), [])
            self.assertEqual(self.r.retrieve("   ", m), [])

    def test_zero_topk_returns_empty(self):
        self.assertEqual(self.r.retrieve("故障码", "bm25", topk=0), [])

    def test_invalid_mode_raises(self):
        """v0.1 未知 mode 会静默落入 rerank 分支，掩盖调用方写错枚举。"""
        with self.assertRaises(P.RetrievalError) as cm:
            self.r.retrieve("故障码", "bm25x")
        self.assertEqual(cm.exception.code, "INVALID_MODE")

    def test_non_string_query_raises(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self.r.retrieve(None)
        self.assertEqual(cm.exception.code, "INVALID_QUERY")

    def test_topk_larger_than_depth_not_truncated(self):
        """v0.1 中 topk=20 / depth=5 会静默只返回 5 条。"""
        got = self.r.retrieve("回查外部状态", "bm25", topk=10, depth=1)
        self.assertGreater(len(got), 1)

    def test_long_query_truncated_not_crashed(self):
        got = self.r.retrieve("故障码" * 500, "bm25", topk=3)
        self.assertLessEqual(len(got), 3)

    def test_all_modes_return_valid_ids(self):
        for m in P.MODES:
            for cid, score in self.r.retrieve("故障码 处置", m, topk=5):
                self.assertIn(cid, self.r.by_id)
                self.assertGreaterEqual(score, 0.0)

    def test_explain_shape(self):
        info = self.r.explain("故障码是什么")
        self.assertEqual(set(info), {"query", "terms", "bm25", "vector", "graph"})
        self.assertIn("FaultCode", info["terms"])

    def test_injected_embedder_is_used(self):
        """注入自定义 Embedder 后检索代码不变——这是「上真实 embedding」的接口保证。"""
        class Fixed(P.Embedder):
            def embed(self, text):
                return [1.0, 0.0]

            def embed_many(self, texts):
                return [[1.0, 0.0] for _ in texts]

        r2 = P.HybridRetriever(self.c.root, embedder=Fixed(), bridge_path=self.bridge)
        self.assertIsInstance(r2.embedder, Fixed)
        self.assertEqual(len(r2.retrieve("任意", "vector", topk=3)), min(3, len(r2.chunks)))


class TestCorpusErrors(unittest.TestCase):
    def test_missing_root(self):
        with self.assertRaises(P.RetrievalError) as cm:
            P.build_chunks(os.path.join(tempfile.gettempdir(), "definitely_not_here_xyz"))
        self.assertEqual(cm.exception.code, "CORPUS_ROOT_INVALID")

    def test_empty_root(self):
        tmp = tempfile.mkdtemp(prefix="empty_")
        try:
            with self.assertRaises(P.RetrievalError) as cm:
                P.build_chunks(tmp)
            self.assertEqual(cm.exception.code, "CORPUS_EMPTY")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unreadable_document_strict_vs_lenient(self):
        tmp = tempfile.mkdtemp(prefix="bad_")
        try:
            os.makedirs(os.path.join(tmp, "docs"))
            bad = os.path.join(tmp, "docs", "architecture.md")
            with io.open(bad, "wb") as f:
                f.write(b"\xff\xfe\x00invalid utf-8 \x80\x81")
            with self.assertRaises(P.RetrievalError) as cm:
                P.build_chunks(tmp, strict=True)
            self.assertEqual(cm.exception.code, "DOC_UNREADABLE")
            with self.assertRaises(P.RetrievalError) as cm:
                P.build_chunks(tmp, strict=False)   # 跳过坏文件后语料为空
            self.assertEqual(cm.exception.code, "CORPUS_EMPTY")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── 评估集与指标 ────────────────────────────────────────────
class TestGoldenValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="golden_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file(self, lines):
        p = os.path.join(self.tmp, "g.jsonl")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return p

    def test_valid(self):
        rows = E.load_golden(self._file([
            '{"qid":"q1","query":"问","category":"c","relevant":["a#x#0"]}']))
        self.assertEqual(len(rows), 1)

    def test_missing_field(self):
        with self.assertRaises(P.RetrievalError) as cm:
            E.load_golden(self._file(['{"qid":"q1","query":"问","relevant":["a"]}']))
        self.assertEqual(cm.exception.code, "GOLDEN_INVALID")

    def test_duplicate_qid(self):
        line = '{"qid":"q1","query":"问","category":"c","relevant":["a#x#0"]}'
        with self.assertRaises(P.RetrievalError) as cm:
            E.load_golden(self._file([line, line]))
        self.assertEqual(cm.exception.code, "GOLDEN_INVALID")

    def test_broken_json_not_silently_skipped(self):
        with self.assertRaises(P.RetrievalError) as cm:
            E.load_golden(self._file([
                '{"qid":"q1","query":"问","category":"c","relevant":["a#x#0"]}',
                "{不是 JSON"]))
        self.assertEqual(cm.exception.code, "GOLDEN_INVALID")

    def test_empty_file(self):
        with self.assertRaises(P.RetrievalError) as cm:
            E.load_golden(self._file([]))
        self.assertEqual(cm.exception.code, "GOLDEN_EMPTY")

    def test_unresolved_reference_detected(self):
        golden = [{"qid": "q1", "query": "问", "category": "c", "relevant": ["ghost#x#0"]}]
        with self.assertRaises(P.RetrievalError) as cm:
            E.validate_golden(golden, {"real#x#0": {}})
        self.assertEqual(cm.exception.code, "GOLDEN_RELEVANT_UNRESOLVED")

    def test_valid_reference_passes(self):
        golden = [{"qid": "q1", "query": "问", "category": "c", "relevant": ["a#x#0"]}]
        rep = E.validate_golden(golden, {"a#x#0": {}})
        self.assertEqual(rep["n"], 1)
        self.assertEqual(rep["categories"], {"c": 1})


class TestMetrics(unittest.TestCase):
    class FakeRetriever:
        def __init__(self, mapping):
            self.mapping = mapping

        def retrieve(self, query, mode="bm25", topk=10, depth=50):
            return [(c, 1.0) for c in self.mapping.get(query, [])[:topk]]

    def test_metrics_match_hand_calculation(self):
        # q1: 正确答案 rank1 → R@1=1, mrr=1
        # q2: 正确答案 rank2 → R@1=0, R@5=1, mrr=0.5
        r = self.FakeRetriever({"问1": ["a", "b"], "问2": ["a", "b"]})
        golden = [{"qid": "q1", "query": "问1", "category": "c", "relevant": ["a"]},
                  {"qid": "q2", "query": "问2", "category": "c", "relevant": ["b"]}]
        agg, per_q = E.evaluate(r, golden, "bm25")
        self.assertEqual(agg["recall@1"], 0.5)
        self.assertEqual(agg["recall@5"], 1.0)
        self.assertEqual(agg["mrr@10"], 0.75)
        self.assertEqual(agg["top1_acc"], 0.5)
        self.assertEqual([x["first_rank"] for x in per_q], [1, 2])

    def test_miss_counts_as_zero(self):
        r = self.FakeRetriever({"问": ["a"]})
        golden = [{"qid": "q1", "query": "问", "category": "c", "relevant": ["z"]}]
        agg, _ = E.evaluate(r, golden, "bm25")
        self.assertEqual(agg["recall@10"], 0.0)
        self.assertEqual(agg["mrr@10"], 0.0)

    def test_partial_multi_relevant(self):
        r = self.FakeRetriever({"问": ["a", "x"]})
        golden = [{"qid": "q1", "query": "问", "category": "c", "relevant": ["a", "b"]}]
        agg, _ = E.evaluate(r, golden, "bm25")
        self.assertEqual(agg["recall@10"], 0.5)

    def test_empty_golden_raises(self):
        with self.assertRaises(P.RetrievalError) as cm:
            E.evaluate(self.FakeRetriever({}), [], "bm25")
        self.assertEqual(cm.exception.code, "GOLDEN_EMPTY")

    def test_category_breakdown(self):
        rows = [{"category": "c1", "recall@1": 1.0, "recall@5": 1.0, "mrr": 1.0, "first_rank": 1},
                {"category": "c2", "recall@1": 0.0, "recall@5": 0.0, "mrr": 0.0, "first_rank": None}]
        out = {x["category"]: x for x in E.category_breakdown(rows)}
        self.assertEqual(out["c1"]["recall@1"], 1.0)
        self.assertEqual(out["c2"]["miss"], 1)


# ── HttpEmbedder（本地 stub 服务，验证协议行为而非模型效果） ──
class _StubHandler(http.server.BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):        # 静音测试输出
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n).decode("utf-8"))
        _StubHandler.hits.append(self.path)
        inputs = req.get("input") or []

        def send(code, payload):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        if self.path == "/bad-json":
            return send(200, b"not json at all")
        if self.path == "/count":
            return send(200, {"data": [{"embedding": [1.0, 0.0]}]})      # 少返回
        if self.path == "/dim":
            return send(200, {"data": [{"embedding": [1.0, 0.0, 0.0]} for _ in inputs]})
        if self.path == "/400":
            return send(400, {"error": "bad request"})
        if self.path == "/tei":
            return send(200, [[3.0, 4.0] for _ in inputs])
        return send(200, {"data": [{"embedding": [1.0, 0.0]} for _ in inputs]})


class TestHttpEmbedder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _StubHandler.hits = []

    def _emb(self, path="/v1/embeddings", **kw):
        return P.HttpEmbedder(base_url=self.base, model="stub", path=path, timeout=5,
                              retries=0, **kw)

    def test_success_and_normalization(self):
        vec = self._emb().embed("门禁")
        self.assertEqual(len(vec), 2)
        self.assertAlmostEqual(sum(x * x for x in vec), 1.0, places=6)

    def test_batch_sends_one_request(self):
        self._emb().embed_many(["a", "b", "c"])
        self.assertEqual(len(_StubHandler.hits), 1)

    def test_batch_size_chunking(self):
        self._emb(batch_size=2).embed_many(["a", "b", "c"])
        self.assertEqual(len(_StubHandler.hits), 2)

    def test_tei_style_response_supported(self):
        vec = self._emb(path="/tei").embed("x")
        self.assertEqual(len(vec), 2)          # [3,4] 归一化后 [0.6, 0.8]
        self.assertAlmostEqual(vec[0], 0.6, places=6)

    def test_bad_json(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._emb(path="/bad-json").embed("x")
        self.assertEqual(cm.exception.code, "EMBEDDING_BAD_RESPONSE")

    def test_count_mismatch(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._emb(path="/count").embed_many(["a", "b"])
        self.assertEqual(cm.exception.code, "EMBEDDING_BAD_RESPONSE")

    def test_dim_mismatch(self):
        with self.assertRaises(P.RetrievalError) as cm:
            self._emb(path="/dim", expect_dim=2).embed("x")
        self.assertEqual(cm.exception.code, "EMBEDDING_DIM_MISMATCH")

    def test_4xx_does_not_retry(self):
        emb = P.HttpEmbedder(base_url=self.base, path="/400", retries=3, timeout=5)
        with self.assertRaises(P.RetrievalError) as cm:
            emb.embed("x")
        self.assertEqual(cm.exception.code, "EMBEDDING_HTTP_ERROR")
        self.assertEqual(len(_StubHandler.hits), 1, "4xx 不应重试，否则只是放大错误")

    def test_unreachable_service(self):
        emb = P.HttpEmbedder(base_url="http://127.0.0.1:1", retries=0, timeout=2)
        with self.assertRaises(P.RetrievalError) as cm:
            emb.embed("x")
        self.assertEqual(cm.exception.code, "EMBEDDING_UNAVAILABLE")

    def test_missing_base_url_fails_fast(self):
        """缺少配置时不允许悄悄退回 TF-IDF——那会让人误以为用了语义检索。"""
        saved = os.environ.pop("EMBEDDING_BASE_URL", None)
        try:
            with self.assertRaises(P.RetrievalError) as cm:
                P.HttpEmbedder(base_url=None)
            self.assertEqual(cm.exception.code, "EMBEDDING_UNAVAILABLE")
        finally:
            if saved is not None:
                os.environ["EMBEDDING_BASE_URL"] = saved


# ── 真实语料冒烟测试（不绑定具体数字） ──────────────────────
class TestRealCorpusSmoke(unittest.TestCase):
    """只断言「能跑通且结构合法」，不断言指标——指标随语料改动而变，属回归看板的事。"""

    @classmethod
    def setUpClass(cls):
        cls.repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls.r = P.HybridRetriever(cls.repo)

    def test_corpus_non_empty(self):
        self.assertGreater(len(self.r.chunks), 50)
        self.assertTrue(self.r.fingerprint)

    def test_bridge_loads_all_concepts(self):
        self.assertGreaterEqual(len(self.r.terms), 20)

    def test_golden_set_valid_against_corpus(self):
        golden = E.load_golden()
        rep = E.validate_golden(golden, self.r.by_id)
        self.assertGreaterEqual(rep["n"], 25)

    def test_all_modes_produce_metrics_in_range(self):
        golden = E.load_golden()[:10]
        for m in P.MODES:
            agg, _ = E.evaluate(self.r, golden, m)
            for key in ("recall@1", "recall@5", "recall@10", "mrr@10", "top1_acc"):
                self.assertGreaterEqual(agg[key], 0.0)
                self.assertLessEqual(agg[key], 1.0)

    def test_ablation_configurations_run(self):
        out = E.run_ablation(self.r, E.load_golden()[:8])
        self.assertEqual([a["mode"] for a in out],
                         ["bm25", "bm25+graph", "bm25+vector", "bm25+vec+graph"])
        for a in out:
            self.assertGreaterEqual(a["recall@10"], 0.0)
            self.assertLessEqual(a["recall@10"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
