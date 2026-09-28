from typing import Any
from unittest import mock

import pytest
from pyatlan.errors import ErrorCode

from src.services.metadata_service import CustomMetadataDefinitionError, ensure_custom_metadata_def
from src.settings.atlan_settings import AtlanSettings


def _settings(**overrides: Any) -> AtlanSettings:
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com", oauth_client_id="id", oauth_client_secret="secret"
    )
    defaults.update(overrides)
    return AtlanSettings(**defaults)


def _not_found_error():
    return ErrorCode.API_TOKEN_NOT_FOUND_BY_NAME.exception_with_parameters("x")


def test_ensure_custom_metadata_def_noop_when_already_exists():
    client = mock.MagicMock()
    client.custom_metadata_cache.get_custom_metadata_def.return_value = mock.MagicMock()

    ensure_custom_metadata_def(client, _settings())

    client.create_typedef.assert_not_called()


def test_ensure_custom_metadata_def_creates_when_missing_and_auto_create_enabled():
    client = mock.MagicMock()
    client.custom_metadata_cache.get_custom_metadata_def.side_effect = _not_found_error()

    ensure_custom_metadata_def(client, _settings(custom_metadata={"type-name": "Witboost", "auto-create": True}))

    client.create_typedef.assert_called_once()
    client.custom_metadata_cache.refresh_cache.assert_called_once()
    created_typedef = client.create_typedef.call_args[0][0]
    assert len(created_typedef.attribute_defs) == 9


def test_ensure_custom_metadata_def_raises_when_missing_and_auto_create_disabled():
    client = mock.MagicMock()
    client.custom_metadata_cache.get_custom_metadata_def.side_effect = _not_found_error()

    with pytest.raises(CustomMetadataDefinitionError):
        ensure_custom_metadata_def(client, _settings(custom_metadata={"type-name": "Witboost", "auto-create": False}))

    client.create_typedef.assert_not_called()


def test_ensure_custom_metadata_def_wraps_atlan_api_errors():
    client = mock.MagicMock()
    client.custom_metadata_cache.get_custom_metadata_def.side_effect = _not_found_error()
    client.create_typedef.side_effect = RuntimeError("boom")

    with pytest.raises(CustomMetadataDefinitionError):
        ensure_custom_metadata_def(client, _settings())
