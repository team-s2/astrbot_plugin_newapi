"""Async client for the new-api administrative API."""

from __future__ import annotations

from typing import Any

import aiohttp


class NewApiError(RuntimeError):
    """An error returned while talking to new-api."""


class NewApiClient:
    """Small authenticated client for the endpoints used by this plugin."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        user_id: int,
        timeout: float = 20,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: Root URL of the new-api instance.
            access_token: new-api user access token.
            user_id: User ID associated with the access token.
            timeout: Total HTTP timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {access_token.strip()}",
            "New-Api-User": str(user_id),
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def get(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        """Request an endpoint and unwrap the standard new-api envelope.

        Args:
            path: API path beginning with a slash.
            params: Optional query parameters.

        Returns:
            The value in the response's ``data`` field.

        Raises:
            NewApiError: If the request fails or new-api rejects it.
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=self.timeout,
                trust_env=True,
            )
        try:
            async with self._session.get(
                f"{self.base_url}{path}", params=params
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as error:
                    body = (await response.text())[:200]
                    raise NewApiError(
                        f"new-api returned HTTP {response.status}: {body or 'empty response'}"
                    ) from error
        except (TimeoutError, aiohttp.ClientError) as error:
            raise NewApiError(f"cannot connect to new-api: {error}") from error

        if response.status >= 400:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise NewApiError(
                f"HTTP {response.status}: {message or 'new-api request failed'}"
            )
        if not isinstance(payload, dict):
            raise NewApiError("new-api returned an invalid response")
        if not payload.get("success"):
            raise NewApiError(str(payload.get("message") or "new-api request failed"))
        return payload.get("data")

    async def _request_json(self, method: str, path: str, payload: dict[str, Any]):
        """Send an authenticated JSON request and unwrap its response."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=self.timeout,
                trust_env=True,
            )
        try:
            async with self._session.request(
                method, f"{self.base_url}{path}", json=payload
            ) as response:
                try:
                    response_payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as error:
                    body = (await response.text())[:200]
                    raise NewApiError(
                        f"new-api returned HTTP {response.status}: {body or 'empty response'}"
                    ) from error
        except (TimeoutError, aiohttp.ClientError) as error:
            raise NewApiError(f"cannot connect to new-api: {error}") from error

        if response.status >= 400:
            message = (
                response_payload.get("message")
                if isinstance(response_payload, dict)
                else None
            )
            raise NewApiError(
                f"HTTP {response.status}: {message or 'new-api request failed'}"
            )
        if not isinstance(response_payload, dict):
            raise NewApiError("new-api returned an invalid response")
        if not response_payload.get("success"):
            raise NewApiError(
                str(response_payload.get("message") or "new-api request failed")
            )
        return response_payload.get("data")

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        """Send an authenticated JSON POST request and unwrap its response."""
        return await self._request_json("POST", path, payload)

    async def put(self, path: str, payload: dict[str, Any]) -> Any:
        """Send an authenticated JSON PUT request and unwrap its response."""
        return await self._request_json("PUT", path, payload)

    async def update_channel_status(self, channel_id: int, status: int) -> bool:
        """Set a channel to enabled (1) or manually disabled (2)."""
        data = await self.post(f"/api/channel/{channel_id}/status", {"status": status})
        if not isinstance(data, bool):
            raise NewApiError("new-api returned an invalid channel status result")
        return data

    async def update_channel_fields(
        self, channel_id: int, fields: dict[str, Any]
    ) -> dict:
        """Patch editable channel fields such as ``weight`` and ``priority``.

        The request only carries the fields to change; new-api keeps the rest
        of the channel record and rebuilds its routing abilities from the
        stored state, so credentials and model lists are never touched.

        Args:
            channel_id: Numeric ID of the channel to update.
            fields: Channel fields to write, e.g. ``{"weight": 3}``.

        Returns:
            The updated channel record.

        Raises:
            NewApiError: If the request fails or the response shape is invalid.
        """
        data = await self.put("/api/channel/", {"id": channel_id, **fields})
        if not isinstance(data, dict):
            raise NewApiError("new-api returned an invalid channel update result")
        return data

    async def all_channels(self) -> list[dict]:
        """Read every channel page for the quota image, without the text-list cap."""
        rows: list[dict] = []
        seen: set[int] = set()
        page = 1
        while True:
            data = await self.get("/api/channel/", {"p": page, "page_size": 100})
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise NewApiError("new-api returned an invalid channel list")
            added = 0
            for item in data["items"]:
                if not isinstance(item, dict) or not item.get("id"):
                    raise NewApiError("new-api returned an invalid channel")
                channel_id = int(item["id"])
                if channel_id not in seen:
                    seen.add(channel_id)
                    rows.append(item)
                    added += 1
            if len(rows) >= int(data.get("total", len(rows))):
                return rows
            if not added:
                raise NewApiError("渠道分页未返回新数据，请重试")
            page += 1

    async def codex_usage(self, channel_id: int) -> dict:
        """Fetch Codex subscription usage for a channel.

        Args:
            channel_id: Codex channel ID.

        Returns:
            Upstream Codex usage payload.

        Raises:
            NewApiError: If the payload is invalid.
        """
        data = await self.get(f"/api/channel/{channel_id}/codex/usage")
        if not isinstance(data, dict):
            raise NewApiError("new-api returned invalid Codex usage data")
        return data

    async def codex_reset_credits(self, channel_id: int) -> dict:
        """Fetch Codex rate-limit reset credits for a channel.

        Args:
            channel_id: Codex channel ID.

        Returns:
            Upstream reset-credit payload.

        Raises:
            NewApiError: If the payload is invalid.
        """
        data = await self.get(f"/api/channel/{channel_id}/codex/usage/reset-credits")
        if not isinstance(data, dict):
            raise NewApiError("new-api returned invalid reset-credit data")
        return data

    async def zhipu_coding_plan_usage(self, channel_id: int) -> dict:
        """Fetch Zhipu Coding Plan subscription usage for a channel."""
        data = await self.get(f"/api/channel/{channel_id}/zhipu/coding-plan/usage")
        if not isinstance(data, dict):
            raise NewApiError("new-api returned invalid Zhipu Coding Plan usage data")
        return data

    async def grok_usage(self, channel_id: int) -> dict:
        """Fetch Grok subscription weekly credits and monthly billing usage."""
        data = await self.get(f"/api/channel/{channel_id}/grok/usage")
        if not isinstance(data, dict):
            raise NewApiError("new-api returned invalid Grok usage data")
        return data

    async def flow(self, start_timestamp: int, end_timestamp: int) -> list[dict]:
        """Fetch dashboard flow rows.

        Args:
            start_timestamp: Inclusive UNIX start timestamp.
            end_timestamp: Inclusive UNIX end timestamp.

        Returns:
            Aggregated flow rows.

        Raises:
            NewApiError: If the payload is invalid.
        """
        data = await self.get(
            "/api/data/flow",
            {
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
        )
        if not isinstance(data, list):
            raise NewApiError("new-api returned invalid flow data")
        return data
