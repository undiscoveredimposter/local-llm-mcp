import argparse
import hmac
import sys

from .config import Settings
from .server import create_server

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class BearerAuth:
    """Pure-ASGI bearer check; non-HTTP scopes (lifespan) pass straight through."""

    def __init__(self, app, token: str):
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(auth, b"Bearer " + self._token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self._app(scope, receive, send)


def main() -> None:
    p = argparse.ArgumentParser(prog="local-gpu-mcp")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()

    settings = Settings.from_env()
    server = create_server(settings)
    if args.transport == "stdio":
        server.run("stdio")
        return

    if args.host not in LOOPBACK and not settings.token:
        sys.exit("refusing to listen beyond loopback without LGM_TOKEN set")
    import uvicorn

    app = server.streamable_http_app()
    if settings.token:
        app = BearerAuth(app, settings.token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
