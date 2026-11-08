"""Connection-bound adapters for one authorized hosted PostgreSQL operation.

This is an internal composition seam only. It is not selected by the MCP
runtime and it does not authenticate callers or create a database connection.
The authorization session factory owns the request transaction and cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from orbit.evidence.model import EvidenceSource, EvidenceType, ReferenceStatus
from orbit.evidence.postgres import PostgresEvidenceLinkedMemoryStore
from orbit.evidence.service import (
    EvidenceLinkedMemoryService,
    EvidenceReferenceResolver,
)
from orbit.mcp_server.authorization import (
    AuthorizedProject,
    AuthenticatedPrincipal,
    ProjectAccessDenied,
)
from orbit.mcp_server.hosted_scope import (
    HostedSourceMessageNotFound,
    HostedScopeResolver,
    ProjectScopedSourceMessage,
    ResolvedProjectScope,
)
from orbit.postgres.authorization import PostgresHostedAuthorizationSession
from orbit.retrieval.postgres_data import PostgresRetrievalData
from orbit.retrieval.service import RetrievalService
from orbit.storage.postgres import PostgresMemoryStore


class _RequestEvidenceAuthorizer:
    """Recheck the immutable identity and permissions bound to one request."""

    def __init__(self, scope: ResolvedProjectScope) -> None:
        self._scope = scope

    def authorize(
        self, principal: AuthenticatedPrincipal | None, project: str, action: str
    ) -> AuthorizedProject:
        if action == "read":
            allowed = self._scope.permissions.intersection({"read", "memory:read"})
        elif action == "write":
            allowed = self._scope.permissions.intersection({"write"})
        else:
            allowed = frozenset()
        if principal is not None or project != self._scope.project_key or not allowed:
            raise ProjectAccessDenied("project access denied")
        return AuthorizedProject(project, action, principal)


class _RequestEvidenceReferenceResolver(EvidenceReferenceResolver):
    """Resolve message evidence using only the current authorized session."""

    def __init__(self, request_resolver, session, scope: ResolvedProjectScope) -> None:
        self._request_resolver = request_resolver
        self._session = session
        self._scope = scope

    def resolve(self, project: str, source: EvidenceSource) -> ReferenceStatus:
        if source.evidence_type is not EvidenceType.MESSAGE:
            return ReferenceStatus.UNRESOLVED
        if project != self._scope.project_key:
            raise ProjectAccessDenied("project access denied")
        try:
            self._request_resolver.require_source_message(
                self._session, self._scope, source.source_id
            )
        except HostedSourceMessageNotFound:
            return ReferenceStatus.MISSING
        return ReferenceStatus.AVAILABLE


@dataclass(frozen=True, slots=True, init=False)
class PostgresRequestUnitOfWork:
    """Scoped persistence and retrieval views over one borrowed connection.

    Construct this only through the authorization session factory. Python code
    in this process is trusted; this API shape prevents accidental application
    composition with standalone adapters, not hostile same-process code.
    """

    _authorization_session: PostgresHostedAuthorizationSession
    _scope: ResolvedProjectScope
    _evidence_authorizer: _RequestEvidenceAuthorizer
    evidence_store: PostgresEvidenceLinkedMemoryStore
    evidence_service: EvidenceLinkedMemoryService
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
        evidence_authorizer = _RequestEvidenceAuthorizer(scope)
        object.__setattr__(instance, "_evidence_authorizer", evidence_authorizer)
        object.__setattr__(
            instance,
            "evidence_store",
            PostgresEvidenceLinkedMemoryStore._for_request(connection, scope),
        )
        evidence_store = instance.evidence_store
        object.__setattr__(
            instance,
            "evidence_service",
            EvidenceLinkedMemoryService(
                evidence_store,
                _RequestEvidenceReferenceResolver(resolver, session, scope),
                authorizer=evidence_authorizer,
            ),
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

    def evidence_project(self, action: Literal["read", "write"]) -> AuthorizedProject:
        """Return a request-bound selector after checking the resolved permission."""
        return self._evidence_authorizer.authorize(
            None, self._scope.project_key, action
        )
