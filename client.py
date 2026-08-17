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
            raise NewApiError(message or f"new-api returned HTTP {response.status}")
        if not isinstance(payload, dict):
            raise NewApiError("new-api returned an invalid response")
        if not payload.get("success"):
            raise NewApiError(str(payload.get("message") or "new-api request failed"))
        return payload.get("data")

    async def list_channels(self, page_size: int = 100) -> tuple[list[dict], int]:
        """Return the first page of channels and the total channel count.

        Args:
            page_size: Number of channels to request, capped by new-api at 100.

        Returns:
            A tuple containing channel rows and total count.

        Raises:
            NewApiError: If the response shape is invalid.
        """
        data = await self.get(
            "/api/channel/", {"p": 1, "page_size": min(page_size, 100)}
        )
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise NewApiError("new-api returned an invalid channel list")
        return data["items"], int(data.get("total", len(data["items"])))

    async def quota_per_unit(self) -> float:
        """Return the number of quota units configured for one USD."""
        data = await self.get("/api/status")
        if not isinstance(data, dict):
            raise NewApiError("new-api returned invalid system status data")
        try:
            value = float(data.get("quota_per_unit"))
        except (TypeError, ValueError) as error:
            raise NewApiError("new-api returned an invalid quota_per_unit") from error
        if value <= 0:
            raise NewApiError("new-api returned a non-positive quota_per_unit")
        return value

    async def find_channel(self, query: str) -> dict:
        """Resolve a channel ID or an exact channel name.

        Args:
            query: Numeric ID or channel name.

        Returns:
            The resolved channel.

        Raises:
            NewApiError: If no unique channel can be resolved.
        """
        query = query.strip()
        if query.isdigit():
            data = await self.get(f"/api/channel/{int(query)}")
            if not isinstance(data, dict):
                raise NewApiError(f"channel {query} was not found")
            return data

        data = await self.get(
            "/api/channel/search",
            {"keyword": query, "p": 1, "page_size": 100},
        )
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise NewApiError("new-api returned invalid channel search results")
        rows = data["items"]
        exact = [
            row
            for row in rows
            if str(row.get("name", "")).casefold() == query.casefold()
        ]
        if len(exact) == 1:
            return exact[0]
        if not rows:
            raise NewApiError(f"未找到渠道：{query}")
        names = "、".join(f"{row.get('name')} (#{row.get('id')})" for row in rows[:8])
        raise NewApiError(f"渠道名称不唯一，请改用 ID：{names}")

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
