"""Connection-bound adapters for one authorized hosted PostgreSQL operation.

This is an internal composition seam only. It is not selected by the MCP
runtime and it does not authenticate callers or create a database connection.
The authorization session factory owns the request transaction and cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass
from orbit.mcp_server.hosted_scope import (
    HostedScopeResolver,
    ProjectScopedSourceMessage,
    ResolvedProjectScope,
)
from orbit.postgres.authorization import PostgresHostedAuthorizationSession
from orbit.evidence.postgres import PostgresEvidenceLinkedMemoryStore
from orbit.retrieval.postgres_data import PostgresRetrievalData
from orbit.retrieval.service import RetrievalService
from orbit.storage.postgres import PostgresMemoryStore


@dataclass(frozen=True, slots=True, init=False)
class PostgresRequestUnitOfWork:
    """Scoped persistence and retrieval views over one borrowed connection.

    Construct this only through the authorization session factory. Python code
    in this process is trusted; this API shape prevents accidental application
    composition with standalone adapters, not hostile same-process code.
    """

    _authorization_session: PostgresHostedAuthorizationSession
    _scope: ResolvedProjectScope
    evidence_store: PostgresEvidenceLinkedMemoryStore
    memory_store: PostgresMemoryStore
    retrieval_data: PostgresRetrievalData
    retrieval_service: RetrievalService
    _resolver: HostedScopeResolver

    @classmethod
    def _bind(
        cls,
        session: PostgresHostedAuthorizationSession,
        resolver: HostedScopeResolver,
        scope: ResolvedProjectScope,
    ) -> PostgresRequestUnitOfWork:
        if not isinstance(scope, ResolvedProjectScope):
            raise TypeError("scope must be issued by HostedScopeResolver")
        connection = session._connection_for_adapters
        instance = object.__new__(cls)
        object.__setattr__(instance, "_authorization_session", session)
        object.__setattr__(instance, "_scope", scope)
        object.__setattr__(
            instance,
            "evidence_store",
            PostgresEvidenceLinkedMemoryStore._for_request(connection, scope),
        )
        object.__setattr__(
            instance, "memory_store", PostgresMemoryStore._for_request(connection, scope)
        )
        retrieval_data = PostgresRetrievalData._for_request(connection, scope)
        object.__setattr__(instance, "retrieval_data", retrieval_data)
        object.__setattr__(
            instance,
            "retrieval_service",
            RetrievalService(retrieval_data=retrieval_data),
        )
        object.__setattr__(instance, "_resolver", resolver)
        return instance

    def require_source_message(self, message_id: str) -> ProjectScopedSourceMessage:
        """Validate source provenance on the same authorized session."""
        return self._resolver.require_source_message(
            self._authorization_session, self._scope, message_id
        )
