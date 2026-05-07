import logging
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import uvicorn  # noqa: E402 (환경변수 설정 후 import)

# stdout/stderr UTF-8 강제 (Windows cp949 방지)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=4000,
        reload=_env_bool("UVICORN_RELOAD", False),
        log_level="info",
    )
