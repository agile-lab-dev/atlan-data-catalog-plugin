from typing import Any
from unittest import mock

from src.models.api_models import EntityReference, SystemErr
from src.services.entity_reference_service import get_entity_reference
from src.settings.atlan_settings import AtlanSettings


def _settings(**overrides: Any) -> AtlanSettings:
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com", oauth_client_id="id", oauth_client_secret="secret"
    )
    defaults.update(overrides)
    return AtlanSettings(**defaults)


@mock.patch("src.services.entity_reference_service.FluentSearch")
@mock.patch("src.services.entity_reference_service.CustomMetadataField")
def test_get_entity_reference_returns_link_for_matching_asset(mock_cm_field_cls, mock_fluent_search_cls):
    client = mock.MagicMock()
    settings = _settings()

    matching_asset = mock.MagicMock(guid="guid-1")
    matching_asset.name = "orders_view"
    mock_search = mock.MagicMock()
    mock_search.where.return_value = mock_search
    mock_search.execute.return_value = [matching_asset]
    mock_fluent_search_cls.return_value = mock_search

    result = get_entity_reference(client, settings, "urn:dmb:cmp:finance:risk-finance:0:orders-output-port")

    assert isinstance(result, EntityReference)
    assert result.reference == "https://tenant.atlan.com/assets/guid-1/overview"


@mock.patch("src.services.entity_reference_service.FluentSearch")
@mock.patch("src.services.entity_reference_service.CustomMetadataField")
def test_get_entity_reference_returns_system_err_when_not_found(mock_cm_field_cls, mock_fluent_search_cls):
    client = mock.MagicMock()
    settings = _settings()

    mock_search = mock.MagicMock()
    mock_search.where.return_value = mock_search
    mock_search.execute.return_value = []
    mock_fluent_search_cls.return_value = mock_search

    result = get_entity_reference(client, settings, "urn:dmb:cmp:finance:risk-finance:0:missing-output-port")

    assert isinstance(result, SystemErr)
    assert "ENTITY_NOT_FOUND" in result.error


@mock.patch("src.services.entity_reference_service.FluentSearch")
@mock.patch("src.services.entity_reference_service.CustomMetadataField")
def test_get_entity_reference_uses_first_match_when_multiple_found(mock_cm_field_cls, mock_fluent_search_cls):
    client = mock.MagicMock()
    settings = _settings()

    first_asset = mock.MagicMock(guid="guid-1")
    first_asset.name = "orders_view"
    second_asset = mock.MagicMock(guid="guid-2")
    second_asset.name = "orders_view_duplicate"
    mock_search = mock.MagicMock()
    mock_search.where.return_value = mock_search
    mock_search.execute.return_value = [first_asset, second_asset]
    mock_fluent_search_cls.return_value = mock_search

    result = get_entity_reference(client, settings, "urn:dmb:cmp:finance:risk-finance:0:orders-output-port")

    assert isinstance(result, EntityReference)
    assert result.reference == "https://tenant.atlan.com/assets/guid-1/overview"


@mock.patch("src.services.entity_reference_service.FluentSearch")
@mock.patch("src.services.entity_reference_service.CustomMetadataField")
def test_get_entity_reference_wraps_search_failure(mock_cm_field_cls, mock_fluent_search_cls):
    client = mock.MagicMock()
    settings = _settings()

    mock_search = mock.MagicMock()
    mock_search.where.return_value = mock_search
    mock_search.execute.side_effect = RuntimeError("boom")
    mock_fluent_search_cls.return_value = mock_search

    result = get_entity_reference(client, settings, "urn:dmb:cmp:finance:risk-finance:0:orders-output-port")

    assert isinstance(result, SystemErr)
    assert "ENTITY_REFERENCE_LOOKUP_FAILED" in result.error
