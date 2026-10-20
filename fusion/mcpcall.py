"""Minimal client for Fusion 360's native MCP (Streamable HTTP at 127.0.0.1:27182/mcp).

    python mcpcall.py tools                      # list tools
    python mcpcall.py read '{"...": ...}'        # fusion_mcp_read
    python mcpcall.py script path/to/script.py   # fusion_mcp_execute featureType=script
    python mcpcall.py script path.py --ro        # readOnly script
"""
import json
import sys
import urllib.request

URL = "http://127.0.0.1:27182/mcp"
_session = None
_id = 0


def _post(payload, session=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if session:
        req.add_header("MCP-Session-Id", session)
    with urllib.request.urlopen(req, timeout=600) as r:
        sid = r.headers.get("MCP-Session-Id")
        body = r.read().decode("utf-8", "replace")
        ctype = r.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        msgs = []
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    msgs.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
        body = msgs[-1] if msgs else None
    elif body.strip():
        body = json.loads(body)
    else:
        body = None
    return body, sid


def rpc(method, params=None, notify=False):
    global _id, _session
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        _id += 1
        msg["id"] = _id
    body, sid = _post(msg, _session)
    if sid:
        _session = sid
    return body


def connect():
    rpc("initialize", {"protocolVersion": "2025-03-26",
                       "capabilities": {},
                       "clientInfo": {"name": "laser-qr", "version": "0.1"}})
    rpc("notifications/initialized", {}, notify=True)


def call(tool, args):
    res = rpc("tools/call", {"name": tool, "arguments": args})
    if res is None:
        return None
    if "error" in res:
        raise RuntimeError(json.dumps(res["error"]))
    out = res["result"]
    texts = [c.get("text", "") for c in out.get("content", []) if c.get("type") == "text"]
    return "\n".join(texts) if texts else json.dumps(out)


def run_script(path, read_only=False):
    code = open(path, encoding="utf-8").read()
    obj = {"script": code}
    if read_only:
        obj["readOnly"] = True
    return call("fusion_mcp_execute", {"featureType": "script", "object": obj})


if __name__ == "__main__":
    connect()
    cmd = sys.argv[1]
    if cmd == "tools":
        res = rpc("tools/list", {})
        for t in res["result"]["tools"]:
            print(t["name"], "-", t.get("description", "")[:200].replace("\n", " "))
    elif cmd == "read":
        print(call("fusion_mcp_read", json.loads(sys.argv[2])))
    elif cmd == "script":
        print(run_script(sys.argv[2], read_only="--ro" in sys.argv))
    elif cmd == "call":
        print(call(sys.argv[2], json.loads(sys.argv[3])))
