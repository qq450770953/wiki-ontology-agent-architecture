# -*- coding: utf-8 -*-
"""evaluate.py — 检索效果评估流水线 v0.2（原型）

产出：Recall@1/3/5/10、MRR@10、首条命中率、P50 延迟，多路消融 + 分类拆解。

用法：
  python prototype/retrieval/evaluate.py                    # 全部模式对比
  python prototype/retrieval/evaluate.py --detail           # 加逐查询明细
  python prototype/retrieval/evaluate.py --by-category      # 加分类拆解
  python prototype/retrieval/evaluate.py --validate         # 只校验标注，不跑检索
  python prototype/retrieval/evaluate.py --suggest "某问题"  # 辅助人工标注：看 top-10
  python prototype/retrieval/evaluate.py --mode bm25 --out r.json
  python prototype/retrieval/evaluate.py --compare r.json   # 与基线比 delta
  python prototype/retrieval/evaluate.py --dump-chunks
  python prototype/retrieval/evaluate.py --embedder http    # 走真实 embedding 服务

指标口径（与岗位要求对齐）：
  Recall@k   —— top-k 覆盖了多少比例的标准答案
  首条命中率 —— top-1 是否命中（岗位表述为「首条命中率」，此处按二元命中计算）
  MRR@10     —— 第一个标准答案的名次倒数均值

边界：语料为本仓文档、标注为人工二元相关，规模小，指标用于回归对比与管道
      正确性验证，不代表生产检索质量。语料指纹与指标必须一起看——语料含被测
      文档，改一行文档就会推动基线。

v0.2 改动：
- 标注校验内建（结构 / qid 唯一 / 标注引用必须能解析到真实 chunk），
  取代此前靠临时脚本检查；
- 输出语料指纹，支持 --compare 做基线 delta，区分「代码改动」与「语料漂移」；
- 错误统一走 RetrievalError 错误码，退出码 2 表示配置/数据错误；
- 新增 --by-category / --validate / --suggest / --embedder。
"""
import argparse
import io
import json
import os
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline as P
from pipeline import RetrievalError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden_set.jsonl")
KS = [1, 3, 5, 10]
REQUIRED_FIELDS = ("qid", "query", "category", "relevant")


# ── 评估集加载与校验 ────────────────────────────────────────
def load_golden(path=GOLDEN):
    """读取评估集并做结构校验。JSON 坏行直接失败，不跳过——
    跳过一行等于悄悄删掉一条评估样本，指标会变好而没人发现。"""
    if not os.path.isfile(path):
        raise RetrievalError("GOLDEN_INVALID", "文件不存在: %s" % path)
    rows, seen = [], set()
    with io.open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                g = json.loads(line)
            except json.JSONDecodeError as e:
                raise RetrievalError("GOLDEN_INVALID", "%s 第 %d 行不是合法 JSON: %s"
                                     % (os.path.basename(path), lineno, e))
            if not isinstance(g, dict):
                raise RetrievalError("GOLDEN_INVALID", "第 %d 行不是对象" % lineno)
            missing = [k for k in REQUIRED_FIELDS if not g.get(k)]
            if missing:
                raise RetrievalError("GOLDEN_INVALID", "第 %d 行缺少字段 %s"
                                     % (lineno, ", ".join(missing)))
            if not isinstance(g["relevant"], list):
                raise RetrievalError("GOLDEN_INVALID", "第 %d 行 relevant 必须是数组" % lineno)
            if g["qid"] in seen:
                raise RetrievalError("GOLDEN_INVALID", "第 %d 行 qid 重复: %s"
                                     % (lineno, g["qid"]))
            seen.add(g["qid"])
            rows.append(g)
    if not rows:
        raise RetrievalError("GOLDEN_EMPTY", path)
    return rows


def validate_golden(golden, by_id):
    """校验标注引用是否都能解析到真实 chunk，并给出分布统计。

    这是「标注错了结论就废了」的机制化防线：q17 曾指向不回答该问题的段落，
    靠人工复核才发现。能自动查的是引用是否存在；「是否真的回答了问题」
    仍然只能人审——所以本函数只保证引用有效，不保证标注正确。
    """
    unresolved, dist = [], Counter()
    for g in golden:
        for cid in g["relevant"]:
            if cid not in by_id:
                unresolved.append((g["qid"], cid))
        dist[g["category"]] += 1
    if unresolved:
        detail = "; ".join("%s -> %s" % (q, c) for q, c in unresolved[:8])
        raise RetrievalError("GOLDEN_RELEVANT_UNRESOLVED",
                             "%d 处标注引用不存在（示例: %s）"
                             % (len(unresolved), detail))
    return {"n": len(golden), "categories": dict(sorted(dist.items())),
            "with_multi_relevant": sum(1 for g in golden if len(g["relevant"]) > 1)}


# ── 指标计算 ────────────────────────────────────────────────
def evaluate(retriever, golden, mode, depth=10):
    per_q, lat = [], []
    for g in golden:
        t0 = time.perf_counter()
        hits = retriever.retrieve(g["query"], mode=mode, topk=depth, depth=50)
        lat.append((time.perf_counter() - t0) * 1000)

        ranked = [cid for cid, _ in hits]
        rel = set(g["relevant"])
        first = next((i + 1 for i, cid in enumerate(ranked) if cid in rel), None)

        row = {"qid": g["qid"], "mode": mode, "category": g["category"],
               "first_rank": first, "top": ranked[:depth],
               "mrr": (1.0 / first) if first and first <= 10 else 0.0,
               "top1_hit": 1 if ranked and ranked[0] in rel else 0}
        for k in KS:
            got = len(rel & set(ranked[:k]))
            row["recall@%d" % k] = got / len(rel) if rel else 0.0
        per_q.append(row)

    if not per_q:
        raise RetrievalError("GOLDEN_EMPTY", "评估集无条目")

    agg = {"mode": mode, "n": len(golden),
           "mrr@10": round(statistics.mean(r["mrr"] for r in per_q), 4),
           "top1_acc": round(statistics.mean(r["top1_hit"] for r in per_q), 4),
           "p50_ms": round(statistics.median(lat), 2)}
    for k in KS:
        agg["recall@%d" % k] = round(statistics.mean(r["recall@%d" % k] for r in per_q), 4)
    return agg, per_q


def category_breakdown(per_q):
    """按类别拆分 Recall@1 / Recall@5 / MRR。整体均值会掩盖某一类全灭。"""
    buckets = {}
    for r in per_q:
        buckets.setdefault(r["category"], []).append(r)
    out = []
    for cat in sorted(buckets):
        rows = buckets[cat]
        out.append({
            "category": cat, "n": len(rows),
            "recall@1": round(statistics.mean(r["recall@1"] for r in rows), 4),
            "recall@5": round(statistics.mean(r["recall@5"] for r in rows), 4),
            "mrr@10": round(statistics.mean(r["mrr"] for r in rows), 4),
            "miss": sum(1 for r in rows if not r["first_rank"]),
        })
    return out


def noise_report(per_q, golden, topn=5):
    """诊断：哪些非相关 chunk 最常被误召回。目录/版本块通常是主要噪声源。"""
    cnt = Counter()
    for row, g in zip(per_q, golden):
        rel = set(g["relevant"])
        for cid in row["top"]:
            if cid not in rel:
                cnt[cid] += 1
    return cnt.most_common(topn)


# ── 消融诊断 ────────────────────────────────────────────────
class _FusedRetriever:
    """按自定义召回路与权重融合的适配器，用于消融对比。

    存在的理由：融合权重若靠人猜，等于把结论建立在参数搜索上（25 条查询上
    搜权重必然过拟合）。留下一路、去掉一路，看指标怎么动，才知道每路是否真的
    在贡献信息，而不是只在贡献噪声。
    """

    def __init__(self, r, routes):
        self.r, self.routes = r, routes          # routes: [(name, fn, weight)]

    def retrieve(self, query, mode="fused", topk=10, depth=50):
        q = P.normalize_query(query)
        if not q:
            return []
        depth = max(depth, topk)
        rankings, weights = [], []
        for name, fn, w in self.routes:
            hits = fn(q)
            if hits:
                rankings.append(hits)
                weights.append(w)
        if not rankings:
            return []
        return P.rrf_fuse(rankings, weights=weights, topk=depth)[:topk]


ABLATIONS = [
    ("bm25", [("bm25", lambda r, q: r.bm25.search(P.tokenize(q), 50), 1.0)]),
    ("bm25+graph", [("bm25", lambda r, q: r.bm25.search(P.tokenize(q), 50), 1.0),
                    ("graph", lambda r, q: r.graph.search(q, 12), 0.35)]),
    ("bm25+vector", [("bm25", lambda r, q: r.bm25.search(P.tokenize(q), 50), 1.0),
                     ("vector", lambda r, q: r.vector.search(q, 50), 1.0)]),
    ("bm25+vec+graph", [("bm25", lambda r, q: r.bm25.search(P.tokenize(q), 50), 1.0),
                        ("vector", lambda r, q: r.vector.search(q, 50), 1.0),
                        ("graph", lambda r, q: r.graph.search(q, 12), 0.35)]),
]


def run_ablation(retriever, golden):
    """逐路留一法：看每一路对融合是正贡献还是负贡献。"""
    out = []
    for label, spec in ABLATIONS:
        # 把 retriever 提前绑定进闭包，避免默认参数在循环里被后一次覆盖
        routes = [(n, (lambda q, f=f, rr=retriever: f(rr, q)), w) for n, f, w in spec]
        adapter = _FusedRetriever(retriever, routes)
        agg, _ = evaluate(adapter, golden, label)
        out.append(agg)
    return out


# ── 基线对比 ────────────────────────────────────────────────
def load_baseline(path):
    with io.open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or "agg" not in doc:
        raise RetrievalError("GOLDEN_INVALID", "基线文件缺少 agg: %s" % path)
    return doc


def compare_with_baseline(base, aggs, fingerprint):
    """输出与基线的 delta。先比指纹再比指标——
    语料变了指标就没有可比性，此时只提示"不可比"，不假装是改进或退化。"""
    lines = []
    if base.get("fingerprint") and base["fingerprint"] != fingerprint:
        lines.append("# WARNING 语料指纹不同 (%s -> %s)，指标不可直接比较"
                     % (base["fingerprint"], fingerprint))
    elif not base.get("fingerprint"):
        lines.append("# 提示 基线无语料指纹（旧格式），无法确认语料是否一致")
    base_map = {a["mode"]: a for a in base["agg"]}
    lines.append("%-14s %8s %8s %8s" % ("mode", "dR@1", "dR@10", "dMRR"))
    for a in aggs:
        b = base_map.get(a["mode"])
        if not b:
            continue
        lines.append("%-14s %+8.3f %+8.3f %+8.3f" % (
            a["mode"], a["recall@1"] - b["recall@1"],
            a["recall@10"] - b["recall@10"], a["mrr@10"] - b["mrr@10"]))
    return "\n".join(lines)


# ── 入口 ────────────────────────────────────────────────────
def build_retriever(args):
    embedder = None
    if args.embedder == "http":
        embedder = P.HttpEmbedder()      # 配置缺失时抛 EMBEDDING_UNAVAILABLE
    return P.HybridRetriever(args.corpus or ROOT, embedder=embedder)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=None, choices=list(P.MODES))
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--by-category", action="store_true")
    ap.add_argument("--ablation", action="store_true", help="逐路留一法消融：每路是正贡献还是噪声")
    ap.add_argument("--dump-chunks", action="store_true")
    ap.add_argument("--validate", action="store_true", help="只校验评估集，不跑检索")
    ap.add_argument("--suggest", default=None, metavar="QUERY", help="辅助标注：看 top-10")
    ap.add_argument("--compare", default=None, metavar="BASELINE_JSON")
    ap.add_argument("--embedder", default="tfidf", choices=["tfidf", "http"])
    ap.add_argument("--corpus", default=None, help="语料根目录，默认为本仓库")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    r = build_retriever(args)
    build_ms = (time.perf_counter() - t0) * 1000

    if args.dump_chunks:
        for c in r.chunks:
            print("%s\t%d\t%s" % (c["chunk_id"], len(c["text"]), c["doc"]))
        print("# chunks=%d docs=%d fingerprint=%s"
              % (len(r.chunks), len({c["doc"] for c in r.chunks}), r.fingerprint))
        return 0

    if args.suggest:
        print("# 人工标注辅助：请判断下面哪些 chunk 真正回答了「%s」" % args.suggest)
        for i, (cid, score) in enumerate(r.retrieve(args.suggest, "bm25", topk=10), 1):
            c = r.by_id[cid]
            print("  %2d. %-56s %.2f | %s"
                  % (i, cid, score, c["text"][:60].replace("\n", " ")))
        print("# 选定后按 golden_set.jsonl 格式登记 qid/query/category/relevant/note")
        return 0

    golden = load_golden()
    rep = validate_golden(golden, r.by_id)

    if args.validate:
        print("# golden OK: %d 条 | 类别 %s | 多条标注 %d 条"
              % (rep["n"], rep["categories"], rep["with_multi_relevant"]))
        print("# corpus: %d chunks / %d docs | fingerprint=%s"
              % (len(r.chunks), len({c["doc"] for c in r.chunks}), r.fingerprint))
        return 0

    modes = [args.mode] if args.mode else list(P.MODES)
    print("# corpus: %d chunks / %d docs | fingerprint: %s | golden: %d queries | index build: %.0f ms"
          % (len(r.chunks), len({c['doc'] for c in r.chunks}), r.fingerprint,
             len(golden), build_ms))

    aggs, details = [], {}
    for m in modes:
        agg, per_q = evaluate(r, golden, m)
        aggs.append(agg)
        details[m] = per_q

    hdr = "%-14s %6s %6s %6s %6s %7s %6s %7s" % (
        "mode", "R@1", "R@3", "R@5", "R@10", "MRR@10", "top1", "p50ms")
    print(hdr)
    print("-" * len(hdr))
    for a in aggs:
        print("%-14s %6.3f %6.3f %6.3f %6.3f %7.3f %6.3f %7.2f" % (
            a["mode"], a["recall@1"], a["recall@3"], a["recall@5"],
            a["recall@10"], a["mrr@10"], a["top1_acc"], a["p50_ms"]))

    best = max(modes, key=lambda m: next(a["mrr@10"] for a in aggs if a["mode"] == m))
    print("\n# best by MRR@10: %s" % best)
    for cid, c in noise_report(details[best], golden):
        print("# noise: %-52s x%d" % (cid, c))

    if args.by_category:
        print("\n# by category (%s)" % best)
        for row in category_breakdown(details[best]):
            print("  %-12s n=%-3d R@1=%.3f R@5=%.3f MRR=%.3f miss=%d"
                  % (row["category"], row["n"], row["recall@1"],
                     row["recall@5"], row["mrr@10"], row["miss"]))

    if args.detail:
        for m in modes:
            print("\n== %s ==" % m)
            for row, g in zip(details[m], golden):
                mark = "OK " if row["first_rank"] == 1 else ("hit" if row["first_rank"] else "MISS")
                print("  %s %-4s rank=%-4s R@5=%.2f | %s"
                      % (mark, row["qid"], row["first_rank"], row["recall@5"], g["query"]))

    if args.ablation:
        print("\n# 逐路留一法消融（RRF，权重同默认配置）")
        hdr2 = "  %-16s %6s %6s %6s %7s" % ("routes", "R@1", "R@5", "R@10", "MRR@10")
        print(hdr2)
        for a in run_ablation(r, golden):
            print("  %-16s %6.3f %6.3f %6.3f %7.3f" % (
                a["mode"], a["recall@1"], a["recall@5"], a["recall@10"], a["mrr@10"]))

    if args.compare:
        print("\n" + compare_with_baseline(load_baseline(args.compare), aggs, r.fingerprint))

    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "fingerprint": r.fingerprint,
                "corpus": {"chunks": len(r.chunks), "docs": len({c["doc"] for c in r.chunks})},
                "golden": rep,
                "agg": aggs, "detail": details,
            }, ensure_ascii=False, indent=1))
        print("\n# written %s" % args.out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RetrievalError as e:
        sys.stderr.write("[error] %s\n" % e)
        sys.exit(2)
    except BrokenPipeError:          # 管道被 head 截断时不要抛栈
        sys.exit(0)
