import os
from typing import Any
from unittest import mock

from pyatlan.errors import NotFoundError
from pyatlan.model.assets import DataDomain
from pyatlan.model.assets import DataProduct as PyatlanDataProduct
from pyatlan.model.enums import EntityStatus

from src.models.api_models import Status1
from src.models.data_product_descriptor import (
    ComponentKind,
    DataContract,
    DataProduct,
    DataSharingAgreement,
    OpenMetadataColumn,
    OutputPort,
)
from src.services.unprovision_service import unprovision_data_product
from src.settings.atlan_settings import AtlanSettings

DOMAIN_QN = "default/domain/abcd1234"
DP_QN = f"{DOMAIN_QN}/product/risk-finance-production-v0"
DISPLAY_NAME = "Risk Finance-v0"


def _settings(**overrides: Any) -> AtlanSettings:
    # Isolated from any real application.yaml in the working directory (whose
    # `unprovision.strategy` would otherwise get deep-merged into this test's
    # defaults by pydantic-settings, polluting the expected "archive" strategy
    # with a local/tenant-specific value like "purge").
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com",
        oauth_client_id="id",
        oauth_client_secret="secret",
        domain_mapping={"finance": DOMAIN_QN},
    )
    defaults.update(overrides)
    with mock.patch.dict(os.environ, {"ATLAN_CONFIG_FILE": "/nonexistent/application.yaml"}):
        return AtlanSettings(**defaults)


def _build_output_port(**overrides) -> OutputPort:
    defaults = dict(
        id="urn:dmb:cmp:finance:risk-finance:0:orders-output-port",
        name="Output Port 1",
        description="An output port",
        specific={
            "catalogNameOP": "risk-finance",
            "schemaNameOP": "orders-output-port",
            "viewNameOP": "orders_view",
        },
        kind=ComponentKind.OUTPUTPORT,
        version="1.0",
        infrastructureTemplateId="infra1",
        outputPortType="SQL",
        technology="Databricks",
        dependsOn=[],
        dataContract=DataContract(schema=[OpenMetadataColumn(name="order_id", dataType="STRING")]),
        dataSharingAgreement=DataSharingAgreement(),
        tags=[],
        semanticLinking=[],
    )
    defaults.update(overrides)
    return OutputPort(**defaults)


def _build_data_product(components, **overrides) -> DataProduct:
    defaults = dict(
        id="urn:dmb:dp:finance:risk-finance:0",
        name="Risk Finance",
        description="A test data product",
        kind="dataproduct",
        domain="finance",
        version="0.1.0",
        environment="production",
        dataProductOwner="john.doe",
        ownerGroup="data-owners",
        devGroup="dev-team",
        specific={},
        components=components,
        tags=[],
    )
    defaults.update(overrides)
    return DataProduct(**defaults)


def _not_found() -> NotFoundError:
    return NotFoundError(mock.MagicMock(http_status_code=404, error_id="x", error_message="not found", user_action=""))  # noqa: E501


def _get_asset_by_qualified_name_router(dp_result_or_exc):
    """Routes DataDomain lookups to a generic success, DataProduct lookups (by
    the deterministically recomputed `DP_QN`) to the given result/exception."""  # noqa: E501

    def side_effect(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock(spec=DataDomain, qualified_name=DOMAIN_QN)
        if qualified_name == DP_QN:
            if isinstance(dp_result_or_exc, Exception):
                raise dp_result_or_exc
            return dp_result_or_exc
        raise AssertionError(f"unexpected qualified_name lookup: {qualified_name}")

    return side_effect


@mock.patch("src.services.unprovision_service.remove_technical_asset")
def test_unprovision_data_product_archives_output_port_and_data_product(mock_remove_asset):
    output_port = _build_output_port()
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings(connection_mapping={"databricks": {"production": "default/databricks/123"}})

    mock_remove_asset.return_value = "asset-guid-1"

    dp_asset = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-1", qualified_name=DP_QN, status=EntityStatus.ACTIVE
    )  # noqa: E501
    client.get_asset_by_qualified_name.side_effect = _get_asset_by_qualified_name_router(dp_asset)

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=False)

    assert result.status == Status1.COMPLETED
    mock_remove_asset.assert_called_once()
    assert mock_remove_asset.call_args.args[3] == "archive"
    # Unprovisioning always tears down the whole DataProduct (docs/technical-details.md
    # §Unprovisioning) — not conditional on any "remaining linked Output Ports" count.
    client.asset.delete_by_guid.assert_called_once_with(["dp-guid-1"])
    client.asset.purge_by_guid.assert_not_called()


@mock.patch("src.services.unprovision_service.remove_technical_asset")
def test_unprovision_data_product_uses_purge_when_remove_data_true(mock_remove_asset):
    output_port = _build_output_port()
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings(connection_mapping={"databricks": {"production": "default/databricks/123"}})

    mock_remove_asset.return_value = None  # idempotent: asset already missing
    dp_asset = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-1", qualified_name=DP_QN, status=EntityStatus.ACTIVE
    )  # noqa: E501
    client.get_asset_by_qualified_name.side_effect = _get_asset_by_qualified_name_router(dp_asset)
    client.asset.search.return_value = []

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=True)

    assert result.status == Status1.COMPLETED
    assert mock_remove_asset.call_args.args[3] == "purge"
    client.asset.purge_by_guid.assert_called_once_with(["dp-guid-1"])


@mock.patch("src.services.unprovision_service.remove_technical_asset")
def test_unprovision_data_product_is_idempotent_when_dataproduct_not_found(mock_remove_asset):
    output_port = _build_output_port()
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings(connection_mapping={"databricks": {"production": "default/databricks/123"}})

    mock_remove_asset.return_value = "asset-guid-1"
    client.get_asset_by_qualified_name.side_effect = _get_asset_by_qualified_name_router(_not_found())
    client.asset.search.return_value = []  # by-name fallback also finds nothing

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=False)

    assert result.status == Status1.COMPLETED
    client.asset.delete_by_guid.assert_not_called()
    client.asset.purge_by_guid.assert_not_called()


@mock.patch("src.services.unprovision_service.remove_technical_asset")
def test_unprovision_data_product_is_idempotent_when_dataproduct_already_archived(mock_remove_asset):
    output_port = _build_output_port()
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings(connection_mapping={"databricks": {"production": "default/databricks/123"}})

    mock_remove_asset.return_value = "asset-guid-1"
    archived_dp = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-1", qualified_name=DP_QN, status=EntityStatus.DELETED
    )  # noqa: E501
    client.get_asset_by_qualified_name.side_effect = _get_asset_by_qualified_name_router(archived_dp)

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=False)

    assert result.status == Status1.COMPLETED
    client.asset.delete_by_guid.assert_not_called()
    client.asset.purge_by_guid.assert_not_called()


@mock.patch("src.services.unprovision_service.remove_technical_asset")
def test_unprovision_data_product_finds_dataproduct_via_by_name_fallback(mock_remove_asset):
    """A DataProduct found/restored under a different qualifiedName during a prior
    provisioning call must still be found and archived here, not silently
    skipped."""  # noqa: E501
    output_port = _build_output_port()
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings(connection_mapping={"databricks": {"production": "default/databricks/123"}})

    mock_remove_asset.return_value = "asset-guid-1"
    drifted_qn = f"{DOMAIN_QN}/product/some-other-slug"
    drifted_dp = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-1", qualified_name=drifted_qn, status=EntityStatus.ACTIVE
    )  # noqa: E501

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock(spec=DataDomain, qualified_name=DOMAIN_QN)
        if qualified_name == DP_QN:
            raise _not_found()
        if qualified_name == drifted_qn:
            return drifted_dp
        raise AssertionError(f"unexpected qualified_name lookup: {qualified_name}")

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = [drifted_dp]

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=False)

    assert result.status == Status1.COMPLETED
    client.asset.delete_by_guid.assert_called_once_with(["dp-guid-1"])


@mock.patch("src.services.unprovision_service.remove_technical_asset")
def test_unprovision_data_product_skips_unsupported_technology(mock_remove_asset):
    output_port = _build_output_port(technology="Unsupported")
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings()

    client.get_asset_by_qualified_name.side_effect = _get_asset_by_qualified_name_router(_not_found())
    client.asset.search.return_value = []

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=False)

    assert result.status == Status1.COMPLETED
    mock_remove_asset.assert_not_called()


def test_unprovision_data_product_wraps_unexpected_error():
    output_port = _build_output_port(technology="Unsupported")
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings()

    # `ensure_domain_exists`'s fast qualifiedName lookup raises a plain (non-
    # NotFoundError) exception, which propagates uncaught out of `_remove_data_product`
    # -> generic UNEXPECTED_ERROR, not a DomainNotFoundError/DataProductUpsertError.
    client.get_asset_by_qualified_name.side_effect = RuntimeError("boom")

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=False)

    assert result.error.startswith("UNEXPECTED_ERROR:")


def test_unprovision_data_product_wraps_dataproduct_removal_failure():
    output_port = _build_output_port(technology="Unsupported")
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings()

    dp_asset = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-1", qualified_name=DP_QN, status=EntityStatus.ACTIVE
    )  # noqa: E501
    client.get_asset_by_qualified_name.side_effect = _get_asset_by_qualified_name_router(dp_asset)
    client.asset.delete_by_guid.side_effect = RuntimeError("boom")

    result = unprovision_data_product(client, settings, data_product, output_port.id, remove_data=False)

    assert result.error.startswith("DATAPRODUCT_UPSERT_FAILED:")
