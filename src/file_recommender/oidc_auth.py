"""Strict OIDC JWT verification using a configured issuer and JWKS endpoint."""

from urllib.parse import urlparse


class InvalidIdentityToken(Exception):
    pass


class IdentityProviderUnavailable(Exception):
    pass


class OIDCTokenVerifier:
    def __init__(self, issuer: str, audience: str, jwks_url: str, jwks_client=None):
        for label, value in (("issuer", issuer), ("JWKS URL", jwks_url)):
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError(f"OIDC {label} must be an HTTPS URL without embedded credentials.")
        if not audience.strip():
            raise ValueError("OIDC audience must not be empty.")

        try:
            import jwt
            from jwt import PyJWKClient
        except ImportError as error:
            raise RuntimeError("Install the auth extra with `pip install -e '.[auth]'` for OIDC support.") from error
        from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

        self.issuer = issuer
        self.audience = audience
        self._jwt = jwt
        self._jwk_connection_error = PyJWKClientConnectionError
        self._jwk_client_error = PyJWKClientError
        self._jwks_client = jwks_client or PyJWKClient(jwks_url, cache_jwk_set=True, timeout=5)

    def authenticate(self, token: str) -> str:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = self._jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "sub"]},
            )
        except self._jwk_connection_error as error:
            raise IdentityProviderUnavailable("OIDC signing keys are unavailable.") from error
        except self._jwk_client_error as error:
            raise InvalidIdentityToken("OIDC token signing key was not found.") from error
        except self._jwt.PyJWTError as error:
            raise InvalidIdentityToken("OIDC token is invalid.") from error
        except Exception as error:
            raise IdentityProviderUnavailable("OIDC verification failed unexpectedly.") from error

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 128:
            raise InvalidIdentityToken("OIDC token subject is invalid.")
        return subject
