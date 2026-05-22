import json
import urllib.request
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/xhs/mcp", tags=["xhs-mcp"])
XHS_MCP_URL = "http://172.17.0.1:18060/mcp"

def mcp_raw(method: str, params: Optional[dict] = None):
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "seo-v4", "version": "1"},
        },
    }
    base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    req = urllib.request.Request(
        XHS_MCP_URL,
        data=json.dumps(init_body).encode(),
        headers=base_headers,
        method="POST",
    )
    res = urllib.request.urlopen(req, timeout=20)
    sid = res.headers.get("Mcp-Session-Id")

    headers = dict(base_headers)
    headers["Mcp-Session-Id"] = sid

    init2 = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    urllib.request.urlopen(
        urllib.request.Request(
            XHS_MCP_URL,
            data=json.dumps(init2).encode(),
            headers=headers,
            method="POST",
        ),
        timeout=20,
    )

    body = {"jsonrpc": "2.0", "id": 3, "method": method}
    if params is not None:
        body["params"] = params

    try:
        txt = urllib.request.urlopen(
            urllib.request.Request(
                XHS_MCP_URL,
                data=json.dumps(body).encode(),
                headers=headers,
                method="POST",
            ),
            timeout=25,
        ).read().decode()
    except Exception as e:
        return {"status": "error", "message": "MCP调用超时或失败", "error": str(e), "method": method, "params": params}

    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            pass
    return {"raw": txt}


class SearchReq(BaseModel):
    keyword: str
    sort_by: str = "综合"
    note_type: str = "不限"
    publish_time: str = "不限"


@router.get("/status")
def status():
    return mcp_raw("tools/call", {"name": "check_login_status", "arguments": {}})


@router.get("/tools")
def tools():
    return mcp_raw("tools/list")


@router.post("/search")
def search(payload: SearchReq):
    return mcp_raw("tools/call", {
        "name": "search_feeds",
        "arguments": {
            "keyword": payload.keyword,
            "filters": {
                "sort_by": payload.sort_by,
                "note_type": payload.note_type,
                "publish_time": payload.publish_time,
            },
        },
    })
