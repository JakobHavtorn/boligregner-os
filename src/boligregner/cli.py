"""CLI entry point for boligregner-os.

Launches the FastAPI app via uvicorn.
"""
from __future__ import annotations

import argparse


def main() -> None:
    """Run the boligregner-os server."""
    parser = argparse.ArgumentParser(
        prog="boligregner",
        description="Open-source Danish realkredit mortgage calculator server.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    args = parser.parse_args()

    import uvicorn

    from .server import app

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
