from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

from fastapi.testclient import TestClient

from file_recommender.api import create_app
from file_recommender.index import IndexStore
from file_recommender.oidc_auth import (
    IdentityProviderUnavailable,
    InvalidIdentityToken,
    OIDCTokenVerifier,
)


ISSUER = "https://identity.example.test/"
AUDIENCE = "file-recommender-api"
JWKS_URL = "https://identity.example.test/keys"


class FakeJWKSClient:
    def __init__(self, key, error=None):
        self.key = key
        self.error = error

    def get_signing_key_from_jwt(self, token):
        if self.error:
            raise self.error
        return SimpleNamespace(key=self.key)


@pytest.fixture(scope="module")
def signing_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key(), wrong_private_key


def make_token(private_key, subject="alice", issuer=ISSUER, audience=AUDIENCE, expires_in=300):
    return jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": subject,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def make_verifier(public_key, jwks_client=None):
    return OIDCTokenVerifier(
        ISSUER,
        AUDIENCE,
        JWKS_URL,
        jwks_client=jwks_client or FakeJWKSClient(public_key),
    )


def test_oidc_verifier_accepts_valid_signature_and_returns_subject(signing_keys):
    private_key, public_key, _ = signing_keys
    verifier = make_verifier(public_key)

    subject = verifier.authenticate(make_token(private_key, subject="user-123"))

    assert subject == "user-123"


@pytest.mark.parametrize(
    "token_options",
    [
        {"expires_in": -10},
        {"issuer": "https://wrong-issuer.example.test/"},
        {"audience": "different-service"},
    ],
)
def test_oidc_verifier_rejects_expired_wrong_issuer_and_wrong_audience(signing_keys, token_options):
    private_key, public_key, _ = signing_keys
    verifier = make_verifier(public_key)

    with pytest.raises(InvalidIdentityToken):
        verifier.authenticate(make_token(private_key, **token_options))


def test_oidc_verifier_rejects_invalid_signature_and_algorithm(signing_keys):
    private_key, public_key, wrong_private_key = signing_keys
    verifier = make_verifier(public_key)
    wrong_signature = make_token(wrong_private_key)
    hmac_token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "alice",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "a-test-hmac-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )

    with pytest.raises(InvalidIdentityToken):
        verifier.authenticate(wrong_signature)
    with pytest.raises(InvalidIdentityToken):
        verifier.authenticate(hmac_token)

    missing_subject = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    with pytest.raises(InvalidIdentityToken):
        verifier.authenticate(missing_subject)


def test_oidc_verifier_distinguishes_jwks_outage(signing_keys):
    _, public_key, _ = signing_keys
    verifier = make_verifier(
        public_key,
        FakeJWKSClient(public_key, PyJWKClientConnectionError("network unavailable")),
    )

    with pytest.raises(IdentityProviderUnavailable):
        verifier.authenticate("any-token")


def test_oidc_subject_controls_index_search_and_file_sharing(tmp_path, signing_keys, monkeypatch):
    private_key, public_key, _ = signing_keys
    monkeypatch.delenv("FILE_RECOMMENDER_AUTH_TOKENS", raising=False)
    store = IndexStore(tmp_path / "index.sqlite3")
    client = TestClient(create_app(store, make_verifier(public_key)))
    source = tmp_path / "alice-files"
    source.mkdir()
    document = source / "release-plan.md"
    document.write_text("private release readiness plan", encoding="utf-8")
    alice_token = make_token(private_key, "alice")
    bob_token = make_token(private_key, "bob")
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    indexed = client.post("/index", json={"directory": str(source)}, headers=alice_headers)
    assert indexed.status_code == 200
    bob_search = client.post(
        "/search",
        json={"query": "private release readiness plan"},
        headers=bob_headers,
    )
    assert bob_search.json()["results"] == []

    share = client.post(
        "/permissions",
        json={"path": str(document), "target_user_id": "bob"},
        headers=alice_headers,
    )
    assert share.status_code == 204
    shared_search = client.post(
        "/search",
        json={"query": "private release readiness plan"},
        headers=bob_headers,
    )
    assert shared_search.json()["results"][0]["name"] == "release-plan.md"
    assert client.get("/users/alice/profile", headers=bob_headers).status_code == 403


def test_oidc_api_rejects_invalid_token_and_fails_closed_on_jwks_outage(tmp_path, signing_keys, monkeypatch):
    _, public_key, _ = signing_keys
    monkeypatch.delenv("FILE_RECOMMENDER_AUTH_TOKENS", raising=False)
    store = IndexStore(tmp_path / "index.sqlite3")
    invalid_client = TestClient(create_app(store, make_verifier(public_key)))

    invalid_response = invalid_client.post(
        "/search",
        json={"query": "project notes"},
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert invalid_response.status_code == 401

    unavailable_verifier = make_verifier(
        public_key,
        FakeJWKSClient(public_key, PyJWKClientConnectionError("network unavailable")),
    )
    unavailable_client = TestClient(create_app(store, unavailable_verifier))
    unavailable_response = unavailable_client.post(
        "/search",
        json={"query": "project notes"},
        headers={"Authorization": "Bearer any-token"},
    )
    assert unavailable_response.status_code == 503


def test_oidc_configuration_must_be_complete_and_exclusive(tmp_path, monkeypatch):
    store = IndexStore(tmp_path / "index.sqlite3")
    monkeypatch.setenv("FILE_RECOMMENDER_OIDC_ISSUER", ISSUER)
    monkeypatch.delenv("FILE_RECOMMENDER_OIDC_AUDIENCE", raising=False)
    monkeypatch.setenv("FILE_RECOMMENDER_OIDC_JWKS_URL", JWKS_URL)

    with pytest.raises(ValueError, match="must be configured together"):
        create_app(store)

    monkeypatch.setenv("FILE_RECOMMENDER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("FILE_RECOMMENDER_AUTH_TOKENS", '{"alice":"a-long-enough-static-token"}')
    with pytest.raises(ValueError, match="not both"):
        create_app(store)


def test_oidc_environment_configuration_builds_verifier(tmp_path, monkeypatch):
    monkeypatch.delenv("FILE_RECOMMENDER_AUTH_TOKENS", raising=False)
    monkeypatch.setenv("FILE_RECOMMENDER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("FILE_RECOMMENDER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("FILE_RECOMMENDER_OIDC_JWKS_URL", JWKS_URL)

    application = create_app(IndexStore(tmp_path / "index.sqlite3"))

    assert application.title == "Intelligent File Recommendation API"
