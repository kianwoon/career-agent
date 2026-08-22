#!/usr/bin/env python3
"""CDP WebSocket proxy that rewrites the websocket URL to the public host.

Run on your Mac (where Brave/CDP lives). Exposes the CDP endpoint on a local
port with the correct websocket URL so it works through ngrok/Cloudflare.

Usage:
  python3 cdp-proxy.py [--listen-port 9999] [--cdp-url http://localhost:9222]
"""

import argparse
import asyncio
import logging
import re

import httpx
import uvicorn
from fastapi import FastAPI, Request
from starlette.websockets import WebSocket, WebSocketDisconnect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("cdp-proxy")

app = FastAPI()
CDP_URL = "http://localhost:9222"
PUBLIC_HOST = "localhost:9999"
AUTH_TOKEN = ""


def _rewrite_ws_url(text: str, public_host: str) -> str:
    return re.sub(
        r"ws://[^/]+(/devtools/[\w/-]+)",
        rf"wss://{public_host}\1",
        text,
    )


def _auth_ok(request: Request) -> bool:
    return (not AUTH_TOKEN) or request.headers.get("X-Auth-Token") == AUTH_TOKEN


@app.get("/json/version")
async def json_version(request: Request):
    if not _auth_ok(request):
        from starlette.responses import JSONResponse
        return JSONResponse(status_code=401, content={"error": "missing X-Auth-Token"})
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CDP_URL}/json/version")
        data = resp.json()
        if "webSocketDebuggerUrl" in data:
            data["webSocketDebuggerUrl"] = _rewrite_ws_url(
                data["webSocketDebuggerUrl"], PUBLIC_HOST
            )
        return data


@app.get("/json")
async def json_list(request: Request):
    if not _auth_ok(request):
        from starlette.responses import JSONResponse
        return JSONResponse(status_code=401, content={"error": "missing X-Auth-Token"})
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CDP_URL}/json")
        data = resp.json()
        for item in data:
            if "webSocketDebuggerUrl" in item:
                item["webSocketDebuggerUrl"] = _rewrite_ws_url(
                    item["webSocketDebuggerUrl"], PUBLIC_HOST
                )
        return data


@app.websocket("/devtools/{path:path}")
async def ws_proxy(websocket: WebSocket, path: str):
    await websocket.accept()
    if AUTH_TOKEN and dict(websocket.headers).get("x-auth-token") != AUTH_TOKEN:
        await websocket.close(code=4001)
        return
    target = f"ws://localhost:9222/devtools/{path}"
    import websockets

    try:
        async with websockets.connect(target, max_size=None) as upstream:
            async def client_to_upstream():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if "bytes" in msg:
                        await upstream.send(msg["bytes"])
                    elif "text" in msg:
                        await upstream.send(msg["text"])

            async def upstream_to_client():
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await websocket.send_bytes(msg)
                    else:
                        await websocket.send_text(msg)

            t1 = asyncio.ensure_future(client_to_upstream())
            t2 = asyncio.ensure_future(upstream_to_client())
            done, pending = await asyncio.wait(
                [t1, t2], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("WS error: %s", e)


@app.get("/")
async def root():
    return {"service": "cdp-proxy", "cdp": CDP_URL, "public_host": PUBLIC_HOST}


def main():
    global CDP_URL, PUBLIC_HOST, AUTH_TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=9999)
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--public-host", default="localhost:9999")
    parser.add_argument("--auth-token", default="")
    args = parser.parse_args()
    CDP_URL = args.cdp_url
    PUBLIC_HOST = args.public_host
    AUTH_TOKEN = args.auth_token
    logger.info(
        "CDP proxy: %s -> public %s (listen :%s) auth=%s",
        CDP_URL, PUBLIC_HOST, args.listen_port, "on" if AUTH_TOKEN else "off",
    )
    uvicorn.run(app, host="0.0.0.0", port=args.listen_port)


if __name__ == "__main__":
    main()
