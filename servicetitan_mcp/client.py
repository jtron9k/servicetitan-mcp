"""Low-level HTTP client for ServiceTitan API v2."""

from __future__ import annotations

import httpx
from .auth import token_manager

API_BASE = "https://api.servicetitan.io"


class ServiceTitanClient:
    """Wraps authenticated requests to the ServiceTitan API."""

    def __init__(
        self,
        app_key: str,
        client_id: str,
        client_secret: str,
        tenant_id: str,
    ):
        self.app_key = app_key
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id

    async def _headers(self) -> dict[str, str]:
        token = await token_manager.get_token(
            self.client_id, self.client_secret, self.tenant_id
        )
        return {
            "Authorization": f"Bearer {token}",
            "ST-App-Key": self.app_key,
            "Content-Type": "application/json",
        }

    async def get(
        self,
        path: str,
        params: dict | None = None,
        timeout: float = 60,
    ) -> dict:
        """GET request. `path` should start with / e.g. /crm/v2/tenant/{tenant}/customers"""
        url = f"{API_BASE}{path}"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()

    async def post(
        self,
        path: str,
        json_body: dict | None = None,
        timeout: float = 60,
    ) -> dict:
        url = f"{API_BASE}{path}"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(url, headers=headers, json=json_body)
            resp.raise_for_status()
            return resp.json()

    async def patch(
        self,
        path: str,
        json_body: dict | None = None,
        timeout: float = 60,
    ) -> dict:
        url = f"{API_BASE}{path}"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.patch(url, headers=headers, json=json_body)
            resp.raise_for_status()
            return resp.json()

    async def put(
        self,
        path: str,
        json_body: dict | None = None,
        timeout: float = 60,
    ) -> dict:
        url = f"{API_BASE}{path}"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.put(url, headers=headers, json=json_body)
            resp.raise_for_status()
            return resp.json()

    # -- Convenience helpers --

    async def list_resource(
        self,
        category: str,
        resource: str,
        page: int = 1,
        page_size: int = 50,
        extra_params: dict | None = None,
    ) -> dict:
        """List a paginated resource.

        category: API category slug (crm, jpm, accounting, dispatch, etc.)
        resource: resource path (customers, jobs, invoices, etc.)
        """
        params = {"page": page, "pageSize": page_size}
        if extra_params:
            params.update(extra_params)
        path = f"/{category}/v2/tenant/{self.tenant_id}/{resource}"
        return await self.get(path, params=params)

    async def get_resource(
        self,
        category: str,
        resource: str,
        resource_id: int,
    ) -> dict:
        """Get a single resource by ID."""
        path = f"/{category}/v2/tenant/{self.tenant_id}/{resource}/{resource_id}"
        return await self.get(path)
