"""`python -m mixassist [--fake|--bridge]` serves MCP over stdio; `--http` runs the SoundPilot API instead."""
from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mixassist", description="MixAssist MCP server for FL Studio")
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument("--fake", action="store_true", help="use the in-memory FakeFL backend (default)")
    backend.add_argument("--bridge", action="store_true", help="use the SysEx bridge to a running FL Studio")
    parser.add_argument("--http", action="store_true", help="serve the SoundPilot HTTP API instead of MCP stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    from mixassist.envfile import load_env

    load_env()
    from mixassist.logctx import configure

    # stdout carries MCP JSON-RPC, so logs go to stderr.
    configure()

    if args.bridge:
        os.environ["SOUNDPILOT_FL"] = "bridge"
    elif args.fake or not os.environ.get("SOUNDPILOT_FL"):
        os.environ["SOUNDPILOT_FL"] = "fake"

    if args.http:
        import uvicorn

        uvicorn.run("mixassist.api:app", host=args.host, port=args.port)
        return

    from mixassist.server import main as serve

    serve()


if __name__ == "__main__":
    main()
