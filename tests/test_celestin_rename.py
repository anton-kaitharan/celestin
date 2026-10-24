"""Product rename: metadata, compatibility aliases and environment variables."""

import importlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_distribution_metadata_uses_celestin():
    assert PYPROJECT["project"]["name"] == "celestin-memory"
    assert "orbit" not in PYPROJECT["project"]["description"].lower()


def test_console_scripts_keep_compatibility_aliases():
    scripts = PYPROJECT["project"]["scripts"]
    server = "orbit.mcp_server.server:main"
    assert scripts["celestin"] == server
    assert scripts["orbit"] == server and scripts["memory-bridge"] == server
    assert scripts["celestin-index"] == "orbit.semantic.cli:main"
    for target in scripts.values():
        module, func = target.split(":")
        assert callable(getattr(importlib.import_module(module), func))


def test_semantic_dependencies_are_optional():
    assert not any("fastembed" in d or "sqlite-vec" in d for d in PYPROJECT["project"]["dependencies"])
    assert len(PYPROJECT["project"]["optional-dependencies"]["semantic"]) == 2


def test_server_display_name():
    from orbit.mcp_server.server import MCPToolHandlers, build_runtime, create_mcp_server
    assert create_mcp_server().name == "Celestin"
    assert MCPToolHandlers(build_runtime()).ping() == "Celestin MCP server is running."


def test_main_runs_composed_server_over_stdio(monkeypatch):
    from orbit.mcp_server import server

    runtime = object()
    app = Mock()
    build = Mock(return_value=runtime)
    create = Mock(return_value=app)
    monkeypatch.setattr(server, "build_runtime", build)
    monkeypatch.setattr(server, "create_mcp_server", create)

    server.main()

    create.assert_called_once_with(runtime)
    app.run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"CELESTIN_DB_PATH": "/new.db", "MEMORY_BRIDGE_DB_PATH": "/old.db"}, "/new.db"),
        ({"MEMORY_BRIDGE_DB_PATH": "/old.db"}, "/old.db"),
        ({}, "data/memory_bridge.db"),
    ],
)
def test_database_path_env_precedence(env, expected):
    # Fresh interpreter: DB_PATH is read at import time, and reloading the
    # module in-process would replace shared exception classes.
    clean = {k: v for k, v in os.environ.items() if k not in ("CELESTIN_DB_PATH", "MEMORY_BRIDGE_DB_PATH")}
    out = subprocess.run(
        [sys.executable, "-c", "from orbit.database.engine import DB_PATH; print(DB_PATH)"],
        env={**clean, **env}, capture_output=True, text=True, check=True,
    )
    assert Path(out.stdout.strip()).as_posix() == Path(expected).as_posix()
