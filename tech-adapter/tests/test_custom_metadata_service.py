from typing import Any
from unittest import mock

import pytest

from src.services.custom_metadata_service import CustomMetadataApplyError, apply_custom_metadata
from src.settings.atlan_settings import AtlanSettings


def _settings(**overrides: Any) -> AtlanSettings:
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com", oauth_client_id="id", oauth_client_secret="secret"
    )
    defaults.update(overrides)
    return AtlanSettings(**defaults)


@mock.patch("src.services.custom_metadata_service.CustomMetadataDict")
def test_apply_custom_metadata_writes_and_verifies(mock_cmd_cls):
    client = mock.MagicMock()
    cmd_instance = {}
    mock_cmd_cls.return_value = cmd_instance

    refreshed_asset = mock.MagicMock()
    refreshed_asset.get_custom_metadata.return_value = {"Domain": "finance"}
    client.get_asset_by_guid.return_value = refreshed_asset

    apply_custom_metadata(client, _settings(), "guid-1", mock.MagicMock, {"Domain": "finance"})

    client.update_custom_metadata_attributes.assert_called_once_with("guid-1", cmd_instance)
    assert cmd_instance == {"Domain": "finance"}


@mock.patch("src.services.custom_metadata_service.CustomMetadataDict")
def test_apply_custom_metadata_wraps_write_failure(mock_cmd_cls):
    client = mock.MagicMock()
    mock_cmd_cls.return_value = {}
    client.update_custom_metadata_attributes.side_effect = RuntimeError("boom")

    with pytest.raises(CustomMetadataApplyError):
        apply_custom_metadata(client, _settings(), "guid-1", mock.MagicMock, {"Domain": "finance"})


@mock.patch("src.services.custom_metadata_service.CustomMetadataDict")
def test_apply_custom_metadata_raises_when_verification_mismatches(mock_cmd_cls):
    client = mock.MagicMock()
    mock_cmd_cls.return_value = {}

    refreshed_asset = mock.MagicMock()
    refreshed_asset.get_custom_metadata.return_value = {"Domain": "wrong-value"}
    client.get_asset_by_guid.return_value = refreshed_asset

    with pytest.raises(CustomMetadataApplyError):
        apply_custom_metadata(client, _settings(), "guid-1", mock.MagicMock, {"Domain": "finance"})


@mock.patch("src.services.custom_metadata_service.CustomMetadataDict")
def test_apply_custom_metadata_wraps_verification_api_error(mock_cmd_cls):
    client = mock.MagicMock()
    mock_cmd_cls.return_value = {}
    client.get_asset_by_guid.side_effect = RuntimeError("boom")

    with pytest.raises(CustomMetadataApplyError):
        apply_custom_metadata(client, _settings(), "guid-1", mock.MagicMock, {"Domain": "finance"})
