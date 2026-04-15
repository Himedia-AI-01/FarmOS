from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
SHOP_BACKEND_DIR = ROOT / "shopping_mall" / "backend"


class BootstrapError(RuntimeError):
    """부트스트랩 공통 예외."""


ROOT_LOG_PREFIX = "Bootstrap"
LOG_PREFIX_WIDTH = 11
_current_log_prefix = ROOT_LOG_PREFIX


def set_log_prefix(prefix: str) -> None:
    global _current_log_prefix
    _current_log_prefix = prefix.strip() or ROOT_LOG_PREFIX


def _format_log_prefix(prefix: str) -> str:
    if prefix == ROOT_LOG_PREFIX:
        return ROOT_LOG_PREFIX
    return prefix.ljust(LOG_PREFIX_WIDTH)


def info(message: str) -> None:
    print(f"[{_format_log_prefix(_current_log_prefix)}] {message}")


def error(message: str) -> None:
    print(
        f"[{_format_log_prefix(_current_log_prefix)}] ERROR: {message}", file=sys.stderr
    )


def load_dotenv(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def normalize_db_url(db_url: str) -> str:
    return re.sub(r"^postgresql\+\w+://", "postgresql://", db_url, count=1)


def parse_database_url(db_url: str) -> Dict[str, str]:
    parsed = urlparse(normalize_db_url(db_url))
    if parsed.scheme != "postgresql":
        raise BootstrapError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")
    if not parsed.hostname or not parsed.username:
        raise BootstrapError("DATABASE_URL must include host and username.")

    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": parsed.username,
        "password": parsed.password or "",
        "database": (parsed.path or "/farmos").lstrip("/") or "farmos",
    }


def detect_database_url(
    explicit_url: str | None = None, prefer: str = "shoppingmall"
) -> str:
    """DATABASE_URL을 우선순위에 따라 탐색한다."""
    if explicit_url:
        return explicit_url

    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    if prefer == "farmos":
        env_candidates = [BACKEND_DIR / ".env", SHOP_BACKEND_DIR / ".env"]
    else:
        env_candidates = [SHOP_BACKEND_DIR / ".env", BACKEND_DIR / ".env"]

    for env_file in env_candidates:
        env_map = load_dotenv(env_file)
        if env_map.get("DATABASE_URL"):
            return env_map["DATABASE_URL"]

    return "postgresql+psycopg2://postgres:root@localhost:5432/farmos"


def resolve_command(command: List[str]) -> List[str]:
    """Windows의 cmd/bat 실행을 안전하게 보정한다."""
    if not command:
        raise BootstrapError("Empty command is not allowed.")

    executable = command[0]
    resolved = shutil.which(executable) or executable
    suffix = Path(resolved).suffix.lower()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", resolved, *command[1:]]
    return [resolved, *command[1:]]


def run_command(
    command: List[str],
    cwd: Path | None = None,
    env_overrides: Dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    resolved = resolve_command(command)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    result = subprocess.run(
        resolved,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture_output,
        check=False,
    )
    if check and result.returncode != 0:
        raise BootstrapError(
            f"Command failed ({result.returncode}): {' '.join(command)}"
        )
    return result


def ensure_tools(*tools: str) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise BootstrapError(
            f"Required tool(s) not found in PATH: {', '.join(missing)}"
        )


def ensure_postgres_running(db_conf: Dict[str, str]) -> None:
    info(f"Checking PostgreSQL server ({db_conf['host']}:{db_conf['port']})...")
    try:
        with socket.create_connection(
            (db_conf["host"], int(db_conf["port"])), timeout=2
        ):
            pass
    except OSError as exc:
        raise BootstrapError(f"PostgreSQL is not reachable: {exc}") from exc

    probe = psql_query(db_conf, "SELECT 1;", database="postgres")
    if probe != "1":
        raise BootstrapError("Failed to verify PostgreSQL connection with SELECT 1.")


def psql_query(db_conf: Dict[str, str], sql: str, database: str | None = None) -> str:
    target_db = database or db_conf["database"]
    result = run_command(
        [
            "psql",
            "-h",
            db_conf["host"],
            "-p",
            db_conf["port"],
            "-U",
            db_conf["user"],
            "-d",
            target_db,
            "-tA",
            "-c",
            sql,
        ],
        env_overrides={"PGPASSWORD": db_conf["password"]},
        capture_output=True,
    )
    return (result.stdout or "").strip()


def ensure_database_exists(db_conf: Dict[str, str]) -> None:
    exists = psql_query(
        db_conf,
        f"SELECT 1 FROM pg_database WHERE datname = '{db_conf['database']}';",
        database="postgres",
    )
    if exists == "1":
        return
    info(f"Database '{db_conf['database']}' not found. Creating...")
    psql_query(
        db_conf, f'CREATE DATABASE "{db_conf["database"]}";', database="postgres"
    )


def table_exists(db_conf: Dict[str, str], table_name: str) -> bool:
    out = psql_query(db_conf, f"SELECT to_regclass('public.{table_name}');")
    return out == table_name


def describe_table_columns(
    db_conf: Dict[str, str], table_name: str
) -> List[Dict[str, str]]:
    safe_table = table_name.replace("'", "''")
    output = psql_query(
        db_conf,
        "SELECT column_name, data_type, is_nullable, "
        "COALESCE(column_default, '') "
        "FROM information_schema.columns "
        f"WHERE table_schema='public' AND table_name='{safe_table}' "
        "ORDER BY ordinal_position;",
    )
    columns: List[Dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        columns.append(
            {
                "name": parts[0].strip(),
                "type": parts[1].strip(),
                "nullable": parts[2].strip(),
                "default": parts[3].strip(),
            }
        )
    return columns


def print_table_summary(db_conf: Dict[str, str], label: str, tables: List[str]) -> None:
    print()
    info(f"{label} 테이블 요약")
    for table in tables:
        if not table_exists(db_conf, table):
            print(f"  - {table}: MISSING")
            continue
        count = psql_query(db_conf, f"SELECT COUNT(*) FROM {table};")
        print(f"  - {table}: {count} rows")
        for column in describe_table_columns(db_conf, table):
            nullability = "NULL" if column["nullable"] == "YES" else "NOT NULL"
            default_part = f", default={column['default']}" if column["default"] else ""
            print(
                f"      - {column['name']}: {column['type']} "
                f"({nullability}{default_part})"
            )
