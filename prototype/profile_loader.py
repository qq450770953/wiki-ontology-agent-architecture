# -*- coding: utf-8 -*-
"""profile_loader.py — 行业本体 Profile 加载器（原型，只读）

职责：加载 docs/interfaces/profile-*.yaml，供 mcp_server.py 查询。
边界：只读、无网络、无副作用；解析失败抛 ProfileError，不静默降级。
"""
import io
import os

import yaml

# 仓库根目录 = prototype/ 的上一级
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(ROOT, "docs", "interfaces")

# 本体版本：与 architecture.md v2.3 对齐；改动须与文档版本块一致
ONTOLOGY_VERSION = "2.3.0"

# 作用域注册表（原型 mock；生产替换为 Policy Service，见 spec §1-2）
SCOPES = {
    ("t1", "agent.demo"),
    ("t1", "agent.analytics"),
    ("t2", "agent.demo"),  # t2 租户仅此主体注册，用于越权用例
}


class ProfileError(Exception):
    """Profile 文件缺失或结构不合法。"""


def check_scope(tenant, subject):
    """校验作用域；未注册一律 UNAUTHORIZED，不泄露存在性。"""
    if (tenant, subject) not in SCOPES:
        raise PermissionError("UNAUTHORIZED")
    return True


def load_profile(profile_id, version):
    """按 id+version 加载 profile。返回 dict；找不到抛 ProfileError。"""
    if version != 1:
        raise ProfileError("VERSION_UNAVAILABLE: profile %s@%s not frozen" % (profile_id, version))
    # profile_id 中的点在文件名中写作连字符：profile.manufacturing.equipment-fault
    #   → profile-manufacturing-equipment-fault-v1.yaml
    fname = "%s-v%d.yaml" % (profile_id.replace(".", "-"), version)
    path = os.path.join(PROFILES_DIR, fname)
    if not os.path.isfile(path):
        raise ProfileError("VERSION_UNAVAILABLE: file missing: %s" % os.path.basename(path))
    with io.open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if doc.get("profile_id") != profile_id:
        raise ProfileError("SCHEMA_MISMATCH: profile_id mismatch")
    return doc


def describe_schema(profile):
    """从 profile 提取类型/关系结构（schema.describe 的数据源）。"""
    domain = profile["layers"]["L2_domain"]["ours"]
    types = []
    for term in domain["term_definitions"]:
        types.append({
            "name": term["en"],
            "term": term["term"],
            "definition": term["def"],
            "aliases": [],
        })
    for alias in domain["aliases"]:
        for t in types:
            if t["name"] == alias["type"]:
                t["aliases"] = alias["aliases"]
    return {
        "ontology_version": ONTOLOGY_VERSION,
        "profile_ref": "%s@%d" % (profile["profile_id"], profile["version"]),
        "types": types,
    }


# 原型 mock 实例数据（生产替换为 SQLite objects 表，spec §8）
MOCK_OBJECTS = [
    {"id": "fault.e102", "type": "FaultCode",
     "properties": {"code": "E102", "severity": "high", "device_family": "splice-A"},
     "effective_from": "2026-01-01", "status": "verified"},
    {"id": "fault.e107", "type": "FaultCode",
     "properties": {"code": "E107", "severity": "medium", "device_family": "splice-A"},
     "effective_from": "2026-01-01", "status": "verified"},
    {"id": "fault.w210", "type": "FaultCode",
     "properties": {"code": "W210", "severity": "low", "device_family": "splice-B"},
     "effective_from": "2026-03-01", "status": "verified"},
    {"id": "station.a3", "type": "Station",
     "properties": {"code": "A3", "line": "L2"},
     "effective_from": "2026-01-01", "status": "verified"},
]

# L3/L5 资产引用（context.assemble 组装用）
TOOL_REFS = [
    {"ref": "tool.fault.lookup", "risk": "L1"},
    {"ref": "tool.remediation.suggest", "risk": "L1"},
    {"ref": "tool.ticket.writeback", "risk": "L3", "human_gate": True},
]
POLICY_REFS = [
    {"type": "policy", "ref": "policy.mes.readonly@2"},
    {"type": "authorization", "ref": "authz.ticket.write@demo", "expires": "2026-11-30"},
]


def query_objects(profile, obj_type, filter_, limit):
    """object.query 的数据源：类型 + 前缀过滤。limit 上限 100（spec §3）。"""
    limit = min(int(limit), 100)
    rows = [o for o in MOCK_OBJECTS if o["type"] == obj_type]
    if not rows:
        raise LookupError("TYPE_NOT_FOUND")
    prefix = (filter_ or {}).get("code_prefix")
    if prefix:
        rows = [o for o in rows
                if o["properties"].get("code", "").startswith(prefix)]
    return {"ontology_version": ONTOLOGY_VERSION,
            "total": len(rows),
            "objects": rows[:limit]}


def assemble_context(profile, intent, budget_tokens, expand_hops):
    """context.assemble 的数据源：按意图关键词选证据，超预算条目进 dropped 带 cut_reason。"""
    expand_hops = min(int(expand_hops), 2)          # spec §4：上限 2 跳
    if int(budget_tokens) < 200:
        raise ValueError("BUDGET_TOO_SMALL")

    evidence, dropped = [], []
    used = 0
    for o in MOCK_OBJECTS:
        cost = 120 if o["type"] == "FaultCode" else 60
        if used + cost <= int(budget_tokens):
            evidence.append({"object_id": o["id"], "source": "mes.gateway",
                             "observed_at": "2026-10-26T09:00:00+08:00",
                             "trust": "observed", "token_count": cost,
                             "cut_reason": None})
            used += cost
        else:
            dropped.append({"object_id": o["id"], "cut_reason": "budget_exceeded"})
    return {
        "trace_id": "ctx_%04x" % (used * 7 % 0xFFFF),  # 原型：伪 trace；生产接 §3.3 trace 体系
        "ontology_version": ONTOLOGY_VERSION,
        "intent": intent,
        "expand_hops": expand_hops,
        "system": "role=analysis_agent",
        "tools": TOOL_REFS,
        "memory": POLICY_REFS,
        "evidence": evidence,
        "dropped": dropped,
        "tokens_used": used,
    }
