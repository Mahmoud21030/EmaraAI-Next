from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser("emaraai-next")
    ap.add_argument("--db", default="data/next.db")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8810)
    a = ap.parse_args()
    import uvicorn

    from .api import build_app
    from .kernel import Kernel
    from .store import Store
    uvicorn.run(build_app(Kernel(Store(a.db))), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
