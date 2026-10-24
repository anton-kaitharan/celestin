"""Configuration-driven Auth0 access-token verification for hosted MCP.

Nothing in this module configures Auth0. The verifier is inert until the
separate hosted runtime is started with an explicit issuer, audience, token
profile, and approved-client allowlist.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import threading
import time
from types import MappingProxyType
from typing import Callable, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from mcp.server.auth.middleware.bearer_auth import AccessToken

from orbit.mcp_server.hosted_scope import PrincipalKind, VerifiedPrincipal
from orbit.mcp_server.hosted_identity import PrincipalAuthenticationFailed


logger = logging.getLogger(__name__)
TokenProfile = Literal["auth0", "rfc9068"]
SigningAlgorithm = Literal["RS256"]
MAX_TOKEN_BYTES = 16_384
MAX_JWKS_BYTES = 1_048_576
MAX_JWKS_KEYS = 100
JWKS_FETCH_ATTEMPTS = 2
JWKS_RETRY_DELAY_SECONDS = 0.1
READ_SCOPE = "memory:read"
WRITE_SCOPE = "memory:write"
_VERIFIED_MARKER = object()


class Auth0ConfigurationError(ValueError):
    """Hosted authentication configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Auth0VerifierConfig:
    issuer_url: str
    audience: str
    token_profile: TokenProfile
    signing_algorithm: SigningAlgorithm
    read_scope: str
    write_scope: str
    approved_clients: Mapping[str, PrincipalKind]

    def __post_init__(self) -> None:
        issuer = self.issuer_url
        parsed = urlsplit(issuer) if isinstance(issuer, str) else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or not issuer.endswith("/")
        ):
            raise Auth0ConfigurationError(
                "Auth0 issuer must be an HTTPS origin ending in '/'."
            )
        if not isinstance(self.audience, str) or not self.audience.strip():
            raise Auth0ConfigurationError("Auth0 API audience is required.")
        if self.token_profile not in ("auth0", "rfc9068"):
            raise Auth0ConfigurationError(
                "Auth0 token profile must be explicitly set to 'auth0' or 'rfc9068'."
            )
        if self.signing_algorithm != "RS256":
            raise Auth0ConfigurationError(
                "The hosted verifier currently supports only the explicitly configured RS256 algorithm."
            )
        if self.read_scope != READ_SCOPE or self.write_scope != WRITE_SCOPE:
            raise Auth0ConfigurationError(
                "Celestin API scopes must be exactly 'memory:read' and 'memory:write'."
            )
        if not isinstance(self.approved_clients, Mapping) or not self.approved_clients:
            raise Auth0ConfigurationError("An approved Auth0 client allowlist is required.")
        clients: dict[str, PrincipalKind] = {}
        for client_id, kind in self.approved_clients.items():
            if not isinstance(client_id, str) or not client_id.strip():
                raise Auth0ConfigurationError("Auth0 client IDs must be non-empty strings.")
            if kind not in ("human", "service"):
                raise Auth0ConfigurationError("Each approved client must map to human or service.")
            clients[client_id] = kind
        object.__setattr__(self, "approved_clients", MappingProxyType(clients))

    @classmethod
    def from_environment(cls) -> Auth0VerifierConfig:
        """Read non-secret verification settings; never print their values."""
        import os

        issuer = os.environ.get("CELESTIN_AUTH0_ISSUER_URL", "")
        audience = os.environ.get("CELESTIN_AUTH0_AUDIENCE", "")
        profile = os.environ.get("CELESTIN_AUTH0_TOKEN_PROFILE", "")
        algorithm = os.environ.get("CELESTIN_AUTH0_SIGNING_ALGORITHM", "")
        raw_clients = os.environ.get("CELESTIN_AUTH0_APPROVED_CLIENTS", "")
        if (
            not issuer or not audience or not profile or not algorithm
            or not raw_clients
        ):
            raise Auth0ConfigurationError(
                "Hosted authentication requires issuer, audience, token profile, "
                "signing algorithm, and an approved-client allowlist."
            )
        try:
            clients = json.loads(raw_clients)
        except (TypeError, json.JSONDecodeError):
            raise Auth0ConfigurationError(
                "The approved Auth0 client allowlist must be a JSON object."
            ) from None
        if not isinstance(clients, dict):
            raise Auth0ConfigurationError(
                "The approved Auth0 client allowlist must be a JSON object."
            )
        return cls(
            issuer, audience, profile, algorithm, READ_SCOPE, WRITE_SCOPE, clients
        )


@dataclass(frozen=True, slots=True)
class _VerifiedClient:
    kind: PrincipalKind


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_json(url: str) -> Mapping[str, object]:
    """Fetch bounded JSON over HTTPS and refuse redirects to other endpoints."""
    request = Request(url, headers={"Accept": "application/json"})
    body = None
    for attempt in range(JWKS_FETCH_ATTEMPTS):
        opener = build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=5.0) as response:
                if response.status != 200:
                    raise ValueError("identity metadata request failed")
                body = response.read(MAX_JWKS_BYTES + 1)
            break
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            exc.close()
            if not retryable or attempt + 1 == JWKS_FETCH_ATTEMPTS:
                raise ValueError("identity metadata request failed") from None
        except (URLError, TimeoutError, OSError):
            if attempt + 1 == JWKS_FETCH_ATTEMPTS:
                raise ValueError("identity metadata request failed") from None
        time.sleep(JWKS_RETRY_DELAY_SECONDS)
    if body is None:
        raise ValueError("identity metadata request failed")
    if len(body) > MAX_JWKS_BYTES:
        raise ValueError("identity metadata response is too large")
    try:
        value = json.loads(body, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("identity metadata response is invalid") from None
    if not isinstance(value, dict):
        raise ValueError("identity metadata response is invalid")
    return value


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


class Auth0TokenVerifier:
    """Verify Auth0-signed API access tokens before MCP dispatch.

    The exact JWT profile is mandatory configuration because Auth0's default
    and RFC 9068 profiles use different client claims and token-type headers.
    The injected fetcher is intended for offline tests and trusted metadata
    retrieval; remote metadata is constrained to the configured HTTPS origin.
    """

    def __init__(
        self,
        config: Auth0VerifierConfig,
        *,
        fetch_json: Callable[[str], Mapping[str, object]] = _fetch_json,
        clock: Callable[[], float] = time.monotonic,
        cache_seconds: int = 300,
    ) -> None:
        if not isinstance(config, Auth0VerifierConfig):
            raise TypeError("config must be Auth0VerifierConfig")
        if not callable(fetch_json) or not callable(clock):
            raise TypeError("fetch_json and clock must be callable")
        if isinstance(cache_seconds, bool) or not isinstance(cache_seconds, int) or cache_seconds < 1:
            raise ValueError("cache_seconds must be a positive integer")
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - optional hosted extra
            raise RuntimeError(
                "Hosted Auth0 authentication requires the hosted optional dependencies."
            ) from exc
        self._jwt = jwt
        self._config = config
        self._fetch_json = fetch_json
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._keys: dict[str, object] = {}
        self._cache_expiry = 0.0
        self._last_refresh = 0.0
        self._last_unknown_kid_refresh = float("-inf")
        self._jwks_uri: str | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return verified MCP auth context; invalid or unavailable is denied."""
        try:
            return await asyncio.to_thread(self._verify_sync, token)
        except Exception as exc:
            # No token, claims, URL, or provider response is written to logs.
            logger.warning("hosted bearer token rejected (%s)", type(exc).__name__)
            return None

    def _verify_sync(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
            return None
        header = self._jwt.get_unverified_header(token)
        if not isinstance(header, dict) or header.get("alg") != self._config.signing_algorithm:
            return None
        if header.get("jku") is not None or header.get("x5u") is not None or header.get("crit"):
            return None
        expected_type = "JWT" if self._config.token_profile == "auth0" else "at+jwt"
        if header.get("typ") != expected_type:
            return None
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 512:
            return None
        key = self._key_for(kid)
        if key is None:
            return None
        client_claim = "azp" if self._config.token_profile == "auth0" else "client_id"
        required = ["iss", "sub", "aud", "exp", "iat", client_claim]
        if self._config.token_profile == "rfc9068":
            required.append("jti")
        else:
            # Auth0's JWT access-token profile uses typ=JWT, like ID tokens.
            # Require a non-empty API scope claim as an additional profile
            # discriminator; an ID token is never a credential for this API.
            required.append("scope")
        claims = self._jwt.decode(
            token,
            key,
            algorithms=[self._config.signing_algorithm],
            issuer=self._config.issuer_url,
            audience=self._config.audience,
            leeway=0,
            options={"require": required, "verify_nbf": True},
        )
        if not isinstance(claims, dict) or not _exact_audience(
            claims.get("aud"), self._config.audience
        ):
            return None
        subject = claims.get("sub")
        issuer = claims.get("iss")
        expires = claims.get("exp")
        issued = claims.get("iat")
        scope_claim = claims.get("scope", "")
        client_id = claims.get(client_claim)
        if (
            not isinstance(subject, str) or not subject.strip()
            or issuer != self._config.issuer_url
            or isinstance(expires, bool) or not isinstance(expires, (int, float))
            or isinstance(issued, bool) or not isinstance(issued, (int, float))
            or not isinstance(scope_claim, str)
            or not isinstance(client_id, str)
            or (self._config.token_profile == "auth0" and not scope_claim.strip())
        ):
            return None
        client = self._config.approved_clients.get(client_id)
        if client is None:
            return None
        if self._config.token_profile == "auth0":
            grant_type = claims.get("gty")
            if client == "service" and grant_type != "client-credentials":
                return None
            if client == "human" and grant_type == "client-credentials":
                return None
        scopes = scope_claim.split()
        if len(scopes) != len(set(scopes)) or any(not scope for scope in scopes):
            return None
        # Bind verifier provenance to the in-process token object consumed by
        # MCP handlers. Tool arguments cannot supply this marker or principal.
        safe_claims = dict(claims)
        safe_claims["_celestin_verifier_marker"] = _VERIFIED_MARKER
        safe_claims["_celestin_principal_kind"] = client
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(expires),
            resource=self._config.audience,
            subject=subject,
            claims=safe_claims,
        )

    def _key_for(self, kid: str):
        now = self._clock()
        with self._lock:
            if now >= self._cache_expiry:
                self._refresh_locked(now)
            key = self._keys.get(kid)
            if key is not None:
                return key
            # A missing kid may indicate signing-key rotation. Refresh at most
            # once per five seconds to prevent unknown-kid request amplification.
            if now - self._last_unknown_kid_refresh < 5:
                return None
            self._last_unknown_kid_refresh = now
            self._refresh_locked(now)
            return self._keys.get(kid)

    def _refresh_locked(self, now: float) -> None:
        issuer_parts = urlsplit(self._config.issuer_url)
        discovery_url = f"{self._config.issuer_url.rstrip('/')}/.well-known/openid-configuration"
        metadata = self._fetch_json(discovery_url)
        if metadata.get("issuer") != self._config.issuer_url:
            raise ValueError("identity metadata issuer mismatch")
        jwks_uri = metadata.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not _same_https_origin(issuer_parts, urlsplit(jwks_uri)):
            raise ValueError("identity signing-key endpoint is not trusted")
        jwks = self._fetch_json(jwks_uri)
        raw_keys = jwks.get("keys")
        if not isinstance(raw_keys, list) or len(raw_keys) > MAX_JWKS_KEYS:
            raise ValueError("identity signing-key set is invalid")
        keys: dict[str, object] = {}
        for raw in raw_keys:
            if not isinstance(raw, dict):
                continue
            raw_kid = raw.get("kid")
            if (
                not isinstance(raw_kid, str) or not raw_kid
                or raw.get("kty") != "RSA"
                or raw.get("use", "sig") != "sig"
                or raw.get("alg", "RS256") != "RS256"
            ):
                continue
            if raw_kid in keys:
                raise ValueError("identity signing-key IDs are duplicated")
            key = self._jwt.PyJWK.from_dict(raw, algorithm="RS256").key
            if getattr(key, "key_size", 0) < 2048:
                continue
            keys[raw_kid] = key
        if not keys:
            raise ValueError("identity signing-key set is empty")
        self._jwks_uri = jwks_uri
        self._keys = keys
        self._last_refresh = now
        self._cache_expiry = now + self._cache_seconds


def _same_https_origin(issuer, jwks) -> bool:
    return (
        issuer.scheme == "https"
        and jwks.scheme == "https"
        and issuer.hostname == jwks.hostname
        and issuer.port == jwks.port
        and jwks.username is None
        and jwks.password is None
        and not jwks.query
        and not jwks.fragment
    )


def _exact_audience(value: object, expected: str) -> bool:
    return value == expected or value == [expected]


class Auth0AccessTokenAuthenticator:
    """Translate only verifier-issued MCP access tokens to trusted principals."""

    def authenticate(self, transport_context: object) -> VerifiedPrincipal:
        if not isinstance(transport_context, AccessToken):
            raise PrincipalAuthenticationFailed("verified access token required")
        claims = transport_context.claims
        if not isinstance(claims, dict) or claims.get("_celestin_verifier_marker") is not _VERIFIED_MARKER:
            raise PrincipalAuthenticationFailed("verified access token required")
        kind = claims.get("_celestin_principal_kind")
        try:
            return VerifiedPrincipal(
                issuer=claims["iss"],
                subject=claims["sub"],
                kind=kind,
            )
        except (KeyError, TypeError, ValueError):
            raise PrincipalAuthenticationFailed("verified access token is invalid") from None
