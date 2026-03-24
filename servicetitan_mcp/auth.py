"""ServiceTitan OAuth2 authentication with automatic token refresh."""

import time
import httpx

TOKEN_URL = "https://auth-integration.servicetitan.io/connect/token"
TOKEN_LIFETIME = 900  # 15 minutes
TOKEN_BUFFER = 60     # refresh 1 minute early


class TokenManager:
    """Manages OAuth2 tokens for one or more ServiceTitan tenants."""

    def __init__(self):
        self._tokens: dict[str, dict] = {}  # keyed by tenant_id

    async def get_token(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
    ) -> str:
        """Return a valid access token, refreshing if needed."""
        cached = self._tokens.get(tenant_id)
        if cached and cached["expires_at"] > time.time():
            return cached["access_token"]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._tokens[tenant_id] = {
            "access_token": data["access_token"],
            "expires_at": time.time() + TOKEN_LIFETIME - TOKEN_BUFFER,
        }
        return data["access_token"]


# Singleton
token_manager = TokenManager()
