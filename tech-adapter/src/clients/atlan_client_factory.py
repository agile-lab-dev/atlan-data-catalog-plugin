from __future__ import annotations

from functools import lru_cache

from pyatlan.client.atlan import AtlanClient

from src.settings.atlan_settings import AtlanSettings


@lru_cache
def get_atlan_settings() -> AtlanSettings:
    """
    Loads `AtlanSettings` once per process from the environment / `.env` file and
    caches it, so repeated dependency injection does not re-parse the environment
    on every request.
    """  # noqa: E501
    return AtlanSettings()  # type: ignore[call-arg]


def build_atlan_client(settings: AtlanSettings) -> AtlanClient:
    """
    Builds an `AtlanClient` authenticated via OAuth 2.0 Client Credentials Grant.

    The SDK (pyatlan>=8.4.4, pinned to 11.0.0 here) owns the token exchange,
    caching, and 401-triggered refresh internally whenever `oauth_client_id` /
    `oauth_client_secret` are provided.
    """  # noqa: E501
    return AtlanClient(
        base_url=settings.base_url,
        oauth_client_id=settings.oauth_client_id,
        oauth_client_secret=settings.oauth_client_secret,
    )


@lru_cache
def get_atlan_client() -> AtlanClient:
    """FastAPI-dependency-friendly, process-wide cached `AtlanClient` instance."""
    return build_atlan_client(get_atlan_settings())
