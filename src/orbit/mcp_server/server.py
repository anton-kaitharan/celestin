"""FastMCP public tools for Celestin."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools.tool_manager import ToolManager

from orbit.database.engine import DatabaseError, ROLE_VALUES
from orbit.repositories._validation import NotFoundError, ValidationError
from orbit.retrieval import SearchIndexUnavailable

from .authorization import (
    READ,
    WRITE,
    ProjectAccessDenied,
    ProjectAccessPolicy,
)
from .persistence_services import PersistenceServices, SourceMessageNotFoundError
from .retrieval_services import RetrievalServices

logger = logging.getLogger(__name__)


class _SafeToolManager(ToolManager):
    """Keep FastMCP's pre-handler validation errors from echoing arguments."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        context=None,
        convert_result: bool = False,
    ):
        try:
            return await super().call_tool(name, arguments, context, convert_result)
        except ToolError as exc:
            # Errors produced by our handlers already use this stable JSON envelope.
            try:
                # FastMCP's Tool.run wraps handler exceptions as
                # "Error executing tool <name>: <original message>".
                # Parse the JSON suffix so stable handler error categories are
                # preserved over the actual MCP protocol as well as direct calls.
                message = str(exc)
                payload = json.loads(message[message.index("{"):])
                code = payload.get("error", {}).get("code")
            except (AttributeError, ValueError, json.JSONDecodeError, TypeError):
                code = None
            if code in {
                "INVALID_ARGUMENT", "SOURCE_MESSAGE_NOT_FOUND", "RETRIEVAL_UNAVAILABLE",
                "STORAGE_ERROR", "INTERNAL_ERROR", "ACCESS_DENIED",
            }:
                raise
            _log_failure("tool_arguments", exc)
            raise _error("INVALID_ARGUMENT", "Required or typed tool arguments are invalid.") from None


ALLOWED_ROLES = set(ROLE_VALUES)
MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 100
# MCP boundary limits are in Unicode code points.
MAX_PROJECT_LENGTH = 200
MAX_CONVERSATION_ID_LENGTH = 512
MAX_MESSAGE_CONTENT_LENGTH = 100_000
MAX_DECISION_KEY_LENGTH = 256
MAX_DECISION_VALUE_LENGTH = 100_000
MAX_SOURCE_MESSAGE_ID_LENGTH = 64
MAX_QUERY_LENGTH = 4_096
MAX_TOKEN_BUDGET = 20_000

# Stdio does not authenticate individual MCP clients. Grants therefore apply
# to this server process only; remote transports need a trusted principal.
def _stdio_principal():
    return None


class _AuthorizedPersistenceServices:
    """Server-facing persistence adapter that authorizes before backend calls."""

    def __init__(self, authorizer: ProjectAccessPolicy, backend: Any) -> None:
        self.authorizer = authorizer
        self.backend = backend

    def _authorize(self, project: str, action: str):
        try:
            return self.authorizer.authorize(_stdio_principal(), project, action)
        except ProjectAccessDenied:
            logger.warning("MCP project authorization denied (%s)", action)
            raise _error("ACCESS_DENIED", "Access to this project is not permitted.") from None

    def ingest_chat_message(self, project: str, conversation_id: str, role: str, content: str):
        scope = self._authorize(project, WRITE)
        return self.backend.ingest_chat_message(scope.project, conversation_id, role, content)

    def save_decision(self, project: str, key: str, value: str, source_message_id: str):
        scope = self._authorize(project, WRITE)
        return self.backend.save_decision(scope.project, key, value, source_message_id)


class _AuthorizedRetrievalServices:
    """Server-facing retrieval adapter that authorizes before backend calls."""

    def __init__(self, authorizer: ProjectAccessPolicy, backend: Any) -> None:
        self.authorizer = authorizer
        self.backend = backend

    def _authorize(self, project: str, action: str):
        try:
            return self.authorizer.authorize(_stdio_principal(), project, action)
        except ProjectAccessDenied:
            logger.warning("MCP project authorization denied (%s)", action)
            raise _error("ACCESS_DENIED", "Access to this project is not permitted.") from None

    def search_chat_history(self, project: str, query: str, limit: int):
        scope = self._authorize(project, READ)
        return self.backend.search_chat_history(scope.project, query, limit)

    def retrieve_context(self, project: str, query: str, token_budget: int):
        scope = self._authorize(project, READ)
        return self.backend.retrieve_context(scope.project, query, token_budget)


def _error(code: str, message: str) -> ToolError:
    """Create an MCP tool error carrying the contract's stable error payload."""
    return ToolError(json.dumps({"error": {"code": code, "message": message}}))


def _required_string(name: str, value: Any, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error("INVALID_ARGUMENT", f"{name} must be a non-empty string.")
    if "\x00" in value:
        raise _error("INVALID_ARGUMENT", f"{name} must not contain NUL characters.")
    if len(value) > max_length:
        raise _error("INVALID_ARGUMENT", f"{name} must not exceed {max_length} characters.")
    return value


def _validate_project(project: str) -> str:
    return _required_string("project", project, MAX_PROJECT_LENGTH)


def _log_failure(tool_name: str, exc: Exception) -> None:
    """Log operational context without logging message content or query text."""
    logger.error("MCP tool %s failed (%s)", tool_name, type(exc).__name__)


def _retrieval_call(tool_name: str, callback, failure_message: str):
    try:
        return callback()
    except ToolError:
        raise
    except SearchIndexUnavailable as exc:
        _log_failure(tool_name, exc)
        raise _error("RETRIEVAL_UNAVAILABLE", "The search index is unavailable.") from exc
    except (DatabaseError, sqlite3.Error) as exc:
        _log_failure(tool_name, exc)
        raise _error("RETRIEVAL_UNAVAILABLE", "The retrieval service is unavailable.") from exc
    except Exception as exc:
        _log_failure(tool_name, exc)
        raise _error("INTERNAL_ERROR", failure_message) from exc


@dataclass(frozen=True)
class ServerRuntime:
    """Dependencies composed once for one MCP server instance."""

    authorizer: ProjectAccessPolicy
    persistence: _AuthorizedPersistenceServices
    retrieval: _AuthorizedRetrievalServices


def build_runtime(
    *,
    db_path: str | Path | None = None,
    project_authorizer: ProjectAccessPolicy | None = None,
    persistence_backend: Any | None = None,
    retrieval_backend: Any | None = None,
) -> ServerRuntime:
    """Build the supported local runtime; SQLite remains the only default backend.

    Construction is lazy: the SQLite connection is opened by service operations,
    not while building the runtime or registering tools. PostgreSQL configuration
    alone does not select or activate a hosted MCP path.
    """
    authorizer = (
        project_authorizer
        if project_authorizer is not None
        else ProjectAccessPolicy.from_environment()
    )
    persistence = (
        persistence_backend
        if persistence_backend is not None
        else PersistenceServices(db_path)
    )
    retrieval = (
        retrieval_backend
        if retrieval_backend is not None
        else RetrievalServices(db_path)
    )
    return ServerRuntime(
        authorizer=authorizer,
        persistence=_AuthorizedPersistenceServices(authorizer, persistence),
        retrieval=_AuthorizedRetrievalServices(authorizer, retrieval),
    )


class MCPToolHandlers:
    """Validate public MCP arguments and dispatch through composed services."""

    def __init__(self, runtime: ServerRuntime) -> None:
        self._runtime = runtime

    def ingest_chat_message(self, project: str, conversation_id: str, role: str, content: str) -> dict[str, Any]:
        """Record a chat message and return its message ID and UTC creation time."""
        project = _validate_project(project)
        conversation_id = _required_string("conversation_id", conversation_id, MAX_CONVERSATION_ID_LENGTH)
        role = _required_string("role", role, 32)
        if role not in ALLOWED_ROLES:
            allowed = ", ".join(sorted(ALLOWED_ROLES))
            raise _error("INVALID_ARGUMENT", f"role must be one of: {allowed}.")
        content = _required_string("content", content, MAX_MESSAGE_CONTENT_LENGTH)
        try:
            return self._runtime.persistence.ingest_chat_message(project, conversation_id, role, content)
        except ToolError:
            raise
        except ValidationError as exc:
            _log_failure("ingest_chat_message", exc)
            raise _error("INVALID_ARGUMENT", "One or more message fields are invalid.") from exc
        except (DatabaseError, sqlite3.Error) as exc:
            _log_failure("ingest_chat_message", exc)
            raise _error("STORAGE_ERROR", "The message could not be saved.") from exc
        except Exception as exc:
            _log_failure("ingest_chat_message", exc)
            raise _error("INTERNAL_ERROR", "The message could not be saved.") from exc

    def save_decision(self, project: str, key: str, value: str, source_message_id: str) -> dict[str, Any]:
        """Save a decision draft linked to a message in the same project."""
        project = _validate_project(project)
        key = _required_string("key", key, MAX_DECISION_KEY_LENGTH)
        value = _required_string("value", value, MAX_DECISION_VALUE_LENGTH)
        source_message_id = _required_string("source_message_id", source_message_id, MAX_SOURCE_MESSAGE_ID_LENGTH)
        try:
            return self._runtime.persistence.save_decision(project, key, value, source_message_id)
        except ToolError:
            raise
        except ValidationError as exc:
            _log_failure("save_decision", exc)
            raise _error("INVALID_ARGUMENT", "One or more decision fields are invalid.") from exc
        except (SourceMessageNotFoundError, NotFoundError) as exc:
            _log_failure("save_decision", exc)
            raise _error("SOURCE_MESSAGE_NOT_FOUND", "The source message was not found in this project.") from exc
        except (DatabaseError, sqlite3.Error) as exc:
            _log_failure("save_decision", exc)
            raise _error("STORAGE_ERROR", "The decision could not be saved.") from exc
        except Exception as exc:
            _log_failure("save_decision", exc)
            raise _error("INTERNAL_ERROR", "The decision could not be saved.") from exc

    def search_chat_history(self, project: str, query: str, limit: int = 10) -> dict[str, Any]:
        """Search project chat history and return ordered matching messages."""
        project = _validate_project(project)
        query = _required_string("query", query, MAX_QUERY_LENGTH)
        if isinstance(limit, bool) or not isinstance(limit, int) or not MIN_SEARCH_LIMIT <= limit <= MAX_SEARCH_LIMIT:
            raise _error("INVALID_ARGUMENT", "limit must be an integer from 1 through 100.")
        return _retrieval_call(
            "search_chat_history",
            lambda: self._runtime.retrieval.search_chat_history(project, query, limit),
            "Chat history could not be searched.",
        )

    def retrieve_context(self, project: str, query: str, token_budget: int) -> dict[str, Any]:
        """Retrieve traceable project context that fits the requested budget."""
        project = _validate_project(project)
        query = _required_string("query", query, MAX_QUERY_LENGTH)
        if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
            raise _error("INVALID_ARGUMENT", "token_budget must be a positive integer.")
        if token_budget > MAX_TOKEN_BUDGET:
            raise _error("INVALID_ARGUMENT", f"token_budget must not exceed {MAX_TOKEN_BUDGET}.")
        return _retrieval_call(
            "retrieve_context",
            lambda: self._runtime.retrieval.retrieve_context(project, query, token_budget),
            "Project context could not be retrieved.",
        )

    def ping(self) -> str:
        """Confirm that the Celestin MCP server is responding."""
        return "Celestin MCP server is running."


def create_mcp_server(runtime: ServerRuntime | None = None) -> FastMCP:
    """Create a Celestin MCP app with handlers bound to one runtime."""
    runtime = runtime or build_runtime()
    handlers = MCPToolHandlers(runtime)
    mcp = FastMCP("Celestin")
    register_mcp_tools(mcp, handlers)
    return mcp


def register_mcp_tools(mcp: FastMCP, handlers: Any) -> None:
    """Register the stable public tool contracts on a FastMCP instance."""
    mcp._tool_manager = _SafeToolManager(warn_on_duplicate_tools=mcp.settings.warn_on_duplicate_tools)
    mcp.tool(description="Record one chat message in a project.")(handlers.ingest_chat_message)
    mcp.tool(description="Save a project decision linked to a source message as a draft.")(handlers.save_decision)
    mcp.tool(description="Search chat messages within one project. Limit must be between 1 and 100.")(handlers.search_chat_history)
    mcp.tool(description="Build project-scoped context within a positive token budget (maximum 20000). Counts may be estimates.")(handlers.retrieve_context)
    mcp.tool(description="Confirm that the Celestin MCP server is responding.")(handlers.ping)


def main() -> None:
    """Run the server over stdio for local MCP clients."""
    create_mcp_server(build_runtime()).run(transport="stdio")


if __name__ == "__main__":
    main()
