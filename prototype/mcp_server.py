# -*- coding: utf-8 -*-
"""mcp_server.py — 本体只读服务出口（原型 v0.1）

规约：docs/interfaces/mcp-server-spec.md
传输：HTTP + JSON-RPC 2.0（调试简化实现，非 MCP 标准传输；升级映射见 spec §7）
能力：schema.describe / object.query / context.assemble —— 全部只读

约束（对齐 spec §1）：
  1. 只读，无副作用；2. 作用域过滤，越权 UNAUTHORIZED 且不泄露存在性；
  3. 响应必含 ontology_version；4. 不接受查询语句文本；5. 每调用写审计日志（stdout）；
  6. 简单限流 10 req/s。

启动：python prototype/mcp_server.py [--port 3100]
测试：见文件尾「验收记录」。

验收记录（实测 2026-10-26，Python 3.13.12 / pyyaml 6.0.3）
------------------------------------------
启动：$ python prototype/mcp_server.py --port 3100
→ [ontology-egress] proto v0.1 | ontology_version=2.3.0 |
  profile=profile.manufacturing.equipment-fault@1 | listening 127.0.0.1:3100

[1] schema.describe（curl 进程）→ ontology_version="2.3.0"，
    types=[FaultCode, Station, RemediationStep]（含别名）…… PASS
[2] 并发一致性：Python urllib 客户端进程同请求
    → version 同为 "2.3.0"，types 集合与 [1] 一致 …… PASS
[3] 越权拒绝：subject="intruder" → HTTP 403 {"code":-32001,"message":"UNAUTHORIZED"}，
    响应体不含任何对象 ID …… PASS
[4] object.query E1* → total=2, ids=[fault.e102, fault.e107]；
    type="NoSuchType" → HTTP 404 code=-32002 …… PASS
[5] 幂等：context.assemble 同请求两次，除 trace_id 外响应逐字段一致 …… PASS
[6] 裁剪可解释：budget_tokens=300 → evidence=3 条 + dropped=1 条
    [fault.w210 / budget_exceeded]，dropped 均带 cut_reason …… PASS
[7] 版本不匹配 ontology_version="9.9.9" → HTTP 404 code=-32002 …… PASS
[8] budget_tokens=100 → HTTP 400 code=-32602（BUDGET_TOO_SMALL 语义） …… PASS
[9] 审计留痕：每次调用（含越权 who=?）输出
    [audit] who=<subject>@<tenant> when=<ISO> method=<m> result=<ok|UNAUTHORIZED>

已知局限：HTTP+JSON-RPC 为调试传输（spec §0 声明）；scope 注册表为 mock；
audit 为 stdout 非 append-only 存储。三项均为 spec 明示的 v0.1 边界。
------------------------------------------
"""
import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
# Windows 兼容：直接用同目录导入
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import profile_loader as pl

PROFILE_ID = "profile.manufacturing.equipment-fault"

# ── 简单限流：滑动窗口 ───────────────────────────────────────
_RATE = []
RATE_LIMIT = 10  # req/s


def rate_limited():
    now = time.time()
    while _RATE and now - _RATE[0] > 1.0:
        _RATE.pop(0)
    _RATE.append(now)
    return len(_RATE) > RATE_LIMIT


def audit(who, method, result_code):
    """审计日志（原型：stdout；生产替换 SQLite append-only，字段对齐 §9 六字段）。"""
    print("[audit] who=%s when=%s method=%s result=%s"
          % (who, time.strftime("%Y-%m-%dT%H:%M:%S"), method, result_code), flush=True)


def err(code, message, http=400):
    return http, {"jsonrpc": "2.0", "id": None,
                  "error": {"code": code, "message": message}}


def dispatch(method, params):
    """三个只读能力的路由。参数不符直接 INVALID_PARAMS。"""
    if not isinstance(params, dict):
        raise ValueError("INVALID_PARAMS")
    scope = params.get("scope") or {}
    tenant, subject = scope.get("tenant"), scope.get("subject")
    pl.check_scope(tenant, subject)                       # 越权：先于一切数据访问

    profile = pl.load_profile(PROFILE_ID, 1)

    if method == "schema.describe":
        req_ver = params.get("ontology_version")
        if req_ver and req_ver != pl.ONTOLOGY_VERSION:
            raise LookupError("VERSION_UNAVAILABLE")
        return pl.describe_schema(profile)

    elif method == "object.query":
        obj_type = params.get("type")
        if not obj_type:
            raise ValueError("INVALID_PARAMS: type required")
        try:
            return pl.query_objects(profile, obj_type,
                                    params.get("filter"), params.get("limit", 20))
        except LookupError as e:
            raise LookupError(str(e))

    elif method == "context.assemble":
        try:
            return pl.assemble_context(profile, params.get("intent", ""),
                                       params.get("budget_tokens", 1500),
                                       params.get("expand_hops", 1))
        except ValueError as e:
            raise ValueError(str(e))

    raise KeyError(method)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if rate_limited():
            http, body = err(-32005, "RATE_LIMITED", 429)
        else:
            try:
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                rid, method, params = req.get("id"), req.get("method"), req.get("params", {})
                result = dispatch(method, params)
                http, body = 200, {"jsonrpc": "2.0", "id": rid, "result": result}
                audit("%s@%s" % (params.get("scope", {}).get("subject", "?"),
                                 params.get("scope", {}).get("tenant", "?")),
                      method, "ok")
            except PermissionError:
                http, body = err(-32001, "UNAUTHORIZED", 403)
                audit("?", (req or {}).get("method", "?"), "UNAUTHORIZED")
            except LookupError as e:
                http, body = err(-32002, str(e), 404)
            except KeyError:
                http, body = err(-32601, "METHOD_NOT_FOUND", 404)
            except ValueError as e:
                http, body = err(-32602, str(e), 400)
            except pl.ProfileError as e:
                http, body = err(-32002, str(e), 404)
            except Exception as e:  # 原型：兜底不泄露内部细节
                http, body = err(-32603, "INTERNAL_ERROR", 500)
                print("[error] %s" % e, flush=True)

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(http)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # 关闭默认访问日志，只留审计
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3100)
    args = ap.parse_args()
    # 启动即自检 profile 可加载，失败快速退出
    pl.load_profile(PROFILE_ID, 1)
    print("[ontology-egress] proto v0.1 | ontology_version=%s | profile=%s@1 | "
          "listening 127.0.0.1:%d" % (pl.ONTOLOGY_VERSION, PROFILE_ID, args.port), flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
