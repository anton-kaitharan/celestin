"""Offline Auth0 JWT verification tests; no provider requests are made."""

from __future__ import annotations

import asyncio
from io import BytesIO
import time
from urllib.error import HTTPError, URLError

import pytest

jwt = pytest.importorskip("jwt")
from cryptography.hazmat.primitives.asymmetric import rsa

from orbit.mcp_server.hosted_auth import (
    Auth0AccessTokenAuthenticator,
    Auth0ConfigurationError,
    Auth0TokenVerifier,
    Auth0VerifierConfig,
)
from orbit.mcp_server import hosted_auth
from orbit.mcp_server.hosted_identity import PrincipalAuthenticationFailed
from orbit.mcp_server.hosted_scope import VerifiedPrincipal


ISSUER = "https://celestin-example.auth0.com/"
AUDIENCE = "https://api.celestin.example"
HUMAN_CLIENT = "human-client-id"
SERVICE_CLIENT = "service-client-id"


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_for(private_key, kid="test-key"):
    value = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return {**value, "kid": kid, "use": "sig", "alg": "RS256"}


def build_verifier(signing_key, *, profile="auth0", key=None, clock=lambda: time.time()):
    key = key or jwk_for(signing_key)
    docs = {
        f"{ISSUER}.well-known/openid-configuration": {
            "issuer": ISSUER,
            "jwks_uri": f"{ISSUER}.well-known/jwks.json",
        },
        f"{ISSUER}.well-known/jwks.json": {"keys": [key]},
    }
    calls = []

    def fetch(url):
        calls.append(url)
        return docs[url]

    config = Auth0VerifierConfig(
        issuer_url=ISSUER,
        audience=AUDIENCE,
        token_profile=profile,
        signing_algorithm="RS256",
        read_scope="memory:read",
        write_scope="memory:write",
        approved_clients={HUMAN_CLIENT: "human", SERVICE_CLIENT: "service"},
    )
    verifier = Auth0TokenVerifier(config, fetch_json=fetch, clock=clock)
    return verifier, calls


def make_token(signing_key, *, profile="auth0", client_id=HUMAN_CLIENT, kid="test-key", **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "auth0|user-123" if client_id == HUMAN_CLIENT else "service|worker-1",
        "aud": AUDIENCE,
        "exp": now + 300,
        "iat": now - 5,
        "scope": "memory:read memory:write",
    }
    if profile == "auth0":
        claims["azp"] = client_id
        if client_id == SERVICE_CLIENT:
            claims["gty"] = "client-credentials"
        headers = {"kid": kid, "typ": "JWT"}
    else:
        claims["client_id"] = client_id
        claims["jti"] = "token-123"
        headers = {"kid": kid, "typ": "at+jwt"}
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256", headers=headers)


@pytest.mark.parametrize("profile", ["auth0", "rfc9068"])
def test_verifies_allowlisted_human_access_token_without_network(signing_key, profile):
    verifier, calls = build_verifier(signing_key, profile=profile)
    token = make_token(signing_key, profile=profile)

    result = asyncio.run(verifier.verify_token(token))

    assert result is not None
    assert result.subject == "auth0|user-123"
    assert result.scopes == ["memory:read", "memory:write"]
    assert result.resource == AUDIENCE
    assert calls == [
        f"{ISSUER}.well-known/openid-configuration",
        f"{ISSUER}.well-known/jwks.json",
    ]
    principal = Auth0AccessTokenAuthenticator().authenticate(result)
    assert principal == VerifiedPrincipal(ISSUER, "auth0|user-123", "human")


def test_rejects_unverified_access_token_in_authenticator(signing_key):
    from mcp.server.auth.middleware.bearer_auth import AccessToken

    with pytest.raises(PrincipalAuthenticationFailed):
        Auth0AccessTokenAuthenticator().authenticate(
            AccessToken(token="opaque", client_id="x", scopes=[], claims={"sub": "forged"})
        )


@pytest.mark.parametrize(
    "claims,client_id",
    [
        ({"aud": "https://other.example"}, HUMAN_CLIENT),
        ({"iss": "https://attacker.example/"}, HUMAN_CLIENT),
        ({"exp": int(time.time()) - 60}, HUMAN_CLIENT),
        ({"nbf": int(time.time()) + 60}, HUMAN_CLIENT),
        ({"azp": "unapproved-client"}, HUMAN_CLIENT),
        ({"scope": None}, HUMAN_CLIENT),
        ({"scope": ""}, HUMAN_CLIENT),
        ({"gty": "client-credentials"}, HUMAN_CLIENT),
        ({"gty": "password"}, SERVICE_CLIENT),
    ],
)
def test_rejects_invalid_or_wrong_profile_claims(signing_key, claims, client_id):
    verifier, _ = build_verifier(signing_key)
    token = make_token(signing_key, client_id=client_id, **claims)

    assert asyncio.run(verifier.verify_token(token)) is None


def test_rejects_bad_signature_and_non_rs256(signing_key):
    verifier, _ = build_verifier(signing_key)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert asyncio.run(
        verifier.verify_token(make_token(other_key))
    ) is None
    now = int(time.time())
    claims = {
        "iss": ISSUER, "sub": "auth0|user", "aud": AUDIENCE,
        "exp": now + 300, "iat": now, "azp": HUMAN_CLIENT,
        "scope": "memory:read",
    }
    hs_token = jwt.encode(claims, "not-a-rsa-key", algorithm="HS256", headers={"kid": "test-key"})
    assert asyncio.run(verifier.verify_token(hs_token)) is None


def test_machine_client_requires_client_credentials_claim(signing_key):
    verifier, _ = build_verifier(signing_key)
    token = make_token(signing_key, client_id=SERVICE_CLIENT, gty="client-credentials")
    result = asyncio.run(verifier.verify_token(token))
    assert result is not None
    assert Auth0AccessTokenAuthenticator().authenticate(result).kind == "service"


def test_auth0_token_without_api_scopes_is_rejected(signing_key):
    verifier, _ = build_verifier(signing_key)
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "auth0|user-123",
        "aud": AUDIENCE,
        "exp": now + 300,
        "iat": now - 5,
        "azp": HUMAN_CLIENT,
    }
    token = jwt.encode(
        claims, signing_key, algorithm="RS256", headers={"kid": "test-key", "typ": "JWT"}
    )
    result = asyncio.run(verifier.verify_token(token))
    assert result is None


def test_metadata_fetch_retries_one_transient_failure(monkeypatch):
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            assert size == hosted_auth.MAX_JWKS_BYTES + 1
            return b'{"issuer":"https://issuer.example/"}'

    class Opener:
        def open(self, request, *, timeout):
            calls.append((request.full_url, timeout))
            if len(calls) == 1:
                raise URLError("synthetic network outage")
            return Response()

    monkeypatch.setattr(hosted_auth, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(hosted_auth.time, "sleep", lambda _delay: None)

    assert hosted_auth._fetch_json("https://issuer.example/metadata") == {
        "issuer": "https://issuer.example/"
    }
    assert calls == [
        ("https://issuer.example/metadata", 5.0),
        ("https://issuer.example/metadata", 5.0),
    ]


def test_metadata_fetch_does_not_retry_redirects(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, *, timeout):
            calls.append(request.full_url)
            raise HTTPError(request.full_url, 302, "redirect", {}, BytesIO(b""))

    monkeypatch.setattr(hosted_auth, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(hosted_auth.time, "sleep", lambda _delay: None)

    with pytest.raises(ValueError, match="metadata request failed"):
        hosted_auth._fetch_json("https://issuer.example/metadata")
    assert calls == ["https://issuer.example/metadata"]


def test_unknown_kid_refreshes_jwks_once_for_key_rotation(signing_key):
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    clock = [1_000.0]
    current_jwks = {"keys": [jwk_for(signing_key)]}
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("openid-configuration"):
            return {"issuer": ISSUER, "jwks_uri": f"{ISSUER}.well-known/jwks.json"}
        return current_jwks

    config = Auth0VerifierConfig(
        ISSUER, AUDIENCE, "auth0", "RS256", "memory:read",
        "memory:write", {HUMAN_CLIENT: "human"}
    )
    verifier = Auth0TokenVerifier(
        config, fetch_json=fetch, clock=lambda: clock[0]
    )
    old_token = make_token(signing_key)
    assert asyncio.run(verifier.verify_token(old_token)) is not None

    current_jwks["keys"].append(jwk_for(new_key, kid="rotated-key"))
    clock[0] += 1
    rotated_token = make_token(new_key, kid="rotated-key")
    assert asyncio.run(verifier.verify_token(rotated_token)) is not None
    assert len(calls) == 4


def test_rfc9068_requires_its_explicit_claim_profile(signing_key):
    verifier, _ = build_verifier(signing_key, profile="rfc9068")
    token = make_token(signing_key, profile="auth0")
    assert asyncio.run(verifier.verify_token(token)) is None


def test_environment_requires_explicit_token_profile_and_client_allowlist(monkeypatch):
    for name in (
        "CELESTIN_AUTH0_ISSUER_URL", "CELESTIN_AUTH0_AUDIENCE",
        "CELESTIN_AUTH0_TOKEN_PROFILE", "CELESTIN_AUTH0_SIGNING_ALGORITHM",
        "CELESTIN_AUTH0_APPROVED_CLIENTS",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(Auth0ConfigurationError, match="issuer, audience"):
        Auth0VerifierConfig.from_environment()


def test_scope_names_are_fixed_by_the_celestin_api_contract():
    with pytest.raises(Auth0ConfigurationError, match="exactly 'memory:read'"):
        Auth0VerifierConfig(
            ISSUER, AUDIENCE, "auth0", "RS256", "read", "write",
            {HUMAN_CLIENT: "human"},
        )


def test_expected_token_profile_requires_its_token_type_header(signing_key):
    verifier, _ = build_verifier(signing_key, profile="rfc9068")
    claims = {
        "iss": ISSUER,
        "sub": "auth0|user-123",
        "aud": AUDIENCE,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()) - 5,
        "client_id": HUMAN_CLIENT,
        "jti": "token-123",
        "scope": "memory:read",
    }
    token = jwt.encode(
        claims, signing_key, algorithm="RS256", headers={"kid": "test-key"}
    )
    assert asyncio.run(verifier.verify_token(token)) is None


def test_metadata_endpoint_must_stay_on_configured_https_origin(signing_key):
    config = Auth0VerifierConfig(
        ISSUER, AUDIENCE, "auth0", "RS256", "memory:read",
        "memory:write", {HUMAN_CLIENT: "human"}
    )
    verifier = Auth0TokenVerifier(
        config,
        fetch_json=lambda url: {
            "issuer": ISSUER,
            "jwks_uri": "https://attacker.example/jwks.json",
        },
    )
    with pytest.raises(ValueError, match="not trusted"):
        verifier._key_for("test-key")
