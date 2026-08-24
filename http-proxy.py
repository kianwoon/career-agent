#!/usr/bin/env python3
"""Minimal HTTP CONNECT proxy that egresses through the Mac's residential IP.

This proxy accepts HTTP CONNECT (and plain HTTP GET/POST) and opens real
connections from the Mac to the target — so LinkedIn sees the Mac's home IP.
It's exposed publicly via the Cloudflare tunnel's HTTP hostname, so the Koyeb
container can reach it as a plain HTTP proxy (Playwright supports HTTP proxy
with basic auth).

Usage:
    python3 http-proxy.py --port 8080

Then expose via Cloudflare tunnel (hostname cdp-proxy.draftproof.app -> http://localhost:8080)
or ngrok. The Koyeb container sets:
    PROXY_URL=http://cdp-proxy.draftproof.app
    PROXY_USERNAME=career-proxy
    PROXY_PASSWORD=<password>
"""

import argparse
import asyncio
import base64
import hmac
import logging
import os
import secrets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("http-proxy")

AUTH_USER = "career-proxy"
AUTH_PASSWORD = os.getenv("HTTP_PROXY_PASSWORD", secrets.token_hex(12))
AUTH_TOKEN = "Basic " + base64.b64encode(f"{AUTH_USER}:{AUTH_PASSWORD}".encode()).decode()


def auth_ok(headers) -> bool:
    return hmac.compare_digest(headers.get("proxy-authorization", ""), AUTH_TOKEN)


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        # Read the request line + headers
        request_line = await asyncio.wait_for(reader.readline(), timeout=15)
        if not request_line:
            writer.close()
            return
        line = request_line.decode(errors="replace").strip()
        logger.info("[%s] %s", peer, line)

        headers = {}
        while True:
            h = await asyncio.wait_for(reader.readline(), timeout=15)
            if not h or h in (b"\r\n", b"\n"):
                break
            try:
                k, _, v = h.decode().partition(":")
                headers[k.strip().lower()] = v.strip()
            except Exception:
                pass

        if not auth_ok(headers):
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                         b"Proxy-Authenticate: Basic realm=\"proxy\"\r\n"
                         b"Content-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        parts = line.split()
        if len(parts) < 3:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        method, target, version = parts[0], parts[1], parts[2]

        # CONNECT host:port — tunnel raw TCP
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            port = int(port or 443)
            try:
                upstream = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=20
                )
            except Exception as e:
                logger.warning("CONNECT %s failed: %s", target, e)
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            u_reader, u_writer = upstream
            # Bidirectional copy
            async def pipe(r, w):
                try:
                    while True:
                        data = await r.read(65536)
                        if not data:
                            break
                        w.write(data)
                        await w.drain()
                except Exception:
                    pass
                finally:
                    try:
                        w.close()
                    except Exception:
                        pass
            await asyncio.gather(
                pipe(reader, u_writer),
                pipe(u_reader, writer),
            )
            return

        # Plain HTTP GET/POST through proxy (rare for browsers, but support it)
        # For simplicity, forward to the target via a new connection.
        try:
            from urllib.parse import urlsplit
            parsed = urlsplit(target)
            host = parsed.hostname or ""
            port = parsed.port or 80
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            u_reader, u_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=20
            )
            req = f"{method} {path} {version}\r\n"
            for k, v in headers.items():
                if k in ("proxy-authorization", "proxy-connection"):
                    continue
                req += f"{k}: {v}\r\n"
            req += "\r\n"
            u_writer.write(req.encode())
            await u_writer.drain()
            while True:
                data = await u_reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
            u_writer.close()
        except Exception as e:
            logger.warning("HTTP proxy error: %s", e)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
    except Exception as e:
        logger.warning("[%s] handler error: %s", peer, e)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"=== HTTP CONNECT proxy on {args.host}:{args.port} ===")
    print(f"Username: {AUTH_USER}")
    print(f"Password: {AUTH_PASSWORD}")
    print(f"Proxy URL: http://{AUTH_USER}:{AUTH_PASSWORD}@127.0.0.1:{args.port}")

    server = await asyncio.start_server(handle_client, args.host, args.port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
