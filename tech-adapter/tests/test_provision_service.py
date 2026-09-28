from typing import Any
from unittest import mock

from src.models.api_models import Status1
from src.models.catalog_asset import AssetIdentity, CatalogAssetDescriptor, CatalogColumnDescriptor
from src.models.data_product_descriptor import (
    ComponentKind,
    DataContract,
    DataProduct,
    DataSharingAgreement,
    OpenMetadataColumn,
    OpenMetadataTagLabel,
    OutputPort,
)
from src.services.asset_service import AssetUpsertResult
from src.services.provision_service import _collect_tag_fqns, provision_data_product
from src.settings.atlan_settings import AtlanSettings


def _settings(**overrides: Any) -> AtlanSettings:
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com", oauth_client_id="id", oauth_client_secret="secret"
    )
    defaults.update(overrides)
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
        dataContract=DataContract(
            schema=[OpenMetadataColumn(name="order_id", dataType="STRING", tags=[OpenMetadataTagLabel(tagFQN="PII")])]
        ),
        dataSharingAgreement=DataSharingAgreement(),
        tags=[OpenMetadataTagLabel(tagFQN="Compensation")],
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
        tags=[OpenMetadataTagLabel(tagFQN="Finance")],
    )
    defaults.update(overrides)
    return DataProduct(**defaults)


def _catalog_asset() -> CatalogAssetDescriptor:
    return CatalogAssetDescriptor(
        connection_identity=AssetIdentity(type_name="Connection", qualified_name="default/databricks/123"),
        database_identity=AssetIdentity(type_name="Database", qualified_name="default/databricks/123/risk-finance"),
        schema_identity=AssetIdentity(
            type_name="Schema", qualified_name="default/databricks/123/risk-finance/orders-output-port"
        ),
        asset_identity=AssetIdentity(
            type_name="View",
            qualified_name="default/databricks/123/risk-finance/orders-output-port/orders_view",
        ),
        name="orders_view",
        description="Orders view",
        columns=[CatalogColumnDescriptor(name="order_id", data_type="STRING", description=None)],
    )


def test_collect_tag_fqns_extracts_all_scopes():
    output_port = _build_output_port()
    data_product = _build_data_product([output_port])

    dp_root, by_output_port, by_column = _collect_tag_fqns(data_product)

    assert dp_root == ["Finance"]
    assert by_output_port[output_port.id] == ["Compensation"]
    assert by_column[output_port.id] == {"order_id": ["PII"]}


@mock.patch("src.services.provision_service.associate_business_terms")
@mock.patch("src.services.provision_service.apply_custom_metadata")
@mock.patch("src.services.provision_service.upsert_data_product")
@mock.patch("src.services.provision_service.upsert_technical_asset")
@mock.patch("src.services.provision_service.ensure_parent_hierarchy")
@mock.patch("src.services.provision_service.OutputPortResolverRegistry")
@mock.patch("src.services.provision_service.ensure_business_term")
@mock.patch("src.services.provision_service.resolve_glossary_name")
@mock.patch("src.services.provision_service.ensure_custom_metadata_def")
def test_provision_data_product_happy_path(
    mock_ensure_cmd,
    mock_resolve_glossary,
    mock_ensure_term,
    mock_registry_cls,
    mock_ensure_hierarchy,
    mock_upsert_asset,
    mock_upsert_dp,
    mock_apply_cmd,
    mock_associate_terms,
):
    output_port = _build_output_port()
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings()

    mock_resolve_glossary.return_value = "finance"
    mock_ensure_term.side_effect = lambda client, settings, glossary, fqn: mock.MagicMock(name=f"term-{fqn}")

    catalog_asset = _catalog_asset()
    mock_registry_cls.return_value.resolve.return_value = catalog_asset

    mock_upsert_asset.return_value = AssetUpsertResult(
        asset_guid="view-guid-1", column_guids={"order_id": "col-guid-1"}
    )  # noqa: E501
    mock_upsert_dp.return_value = "dp-guid-1"

    result = provision_data_product(client, settings, data_product, output_port.id)

    assert result.status == Status1.COMPLETED
    mock_ensure_cmd.assert_called_once_with(client, settings)
    mock_ensure_hierarchy.assert_called_once_with(client, catalog_asset)
    mock_upsert_asset.assert_called_once_with(client, catalog_asset)
    # Custom metadata applied to the asset + its one column.
    assert mock_apply_cmd.call_count == 2
    # Term association: asset-level (Compensation) + column-level (PII) + DataProduct-level (Finance).
    assert mock_associate_terms.call_count == 3
    mock_upsert_dp.assert_called_once_with(
        client,
        settings,
        data_product,
        output_port_guids=["view-guid-1"],
        output_port_qualified_names=[catalog_asset.asset_identity.qualified_name],
        witboost_link=None,
    )
    assert output_port.id in result.info.publicInfo
    assert data_product.id in result.info.publicInfo


@mock.patch("src.services.provision_service.associate_business_terms")
@mock.patch("src.services.provision_service.apply_custom_metadata")
@mock.patch("src.services.provision_service.upsert_data_product")
@mock.patch("src.services.provision_service.ensure_source_system_link")
@mock.patch("src.services.provision_service.upsert_technical_asset")
@mock.patch("src.services.provision_service.ensure_parent_hierarchy")
@mock.patch("src.services.provision_service.OutputPortResolverRegistry")
@mock.patch("src.services.provision_service.ensure_business_term")
@mock.patch("src.services.provision_service.resolve_glossary_name")
@mock.patch("src.services.provision_service.ensure_custom_metadata_def")
def test_provision_data_product_attaches_source_system_link_when_info_present(
    mock_ensure_cmd,
    mock_resolve_glossary,
    mock_ensure_term,
    mock_registry_cls,
    mock_ensure_hierarchy,
    mock_upsert_asset,
    mock_ensure_link,
    mock_upsert_dp,
    mock_apply_cmd,
    mock_associate_terms,
):
    href = "https://adb-123.azuredatabricks.net/explore/data/catalog/schema/orders_view"
    output_port = _build_output_port(
        info={"publicInfo": {"tableUrl": {"href": href}}, "privateInfo": {}},
    )
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings()

    mock_resolve_glossary.return_value = "finance"
    mock_ensure_term.side_effect = lambda client, settings, glossary, fqn: mock.MagicMock(name=f"term-{fqn}")

    catalog_asset = _catalog_asset()
    mock_registry_cls.return_value.resolve.return_value = catalog_asset
    mock_upsert_asset.return_value = AssetUpsertResult(
        asset_guid="view-guid-1", column_guids={"order_id": "col-guid-1"}
    )  # noqa: E501
    mock_upsert_dp.return_value = "dp-guid-1"

    provision_data_product(client, settings, data_product, output_port.id)

    mock_ensure_link.assert_called_once_with(
        client,
        mock.ANY,
        catalog_asset.asset_identity.qualified_name,
        catalog_asset.name,
        href,
    )


@mock.patch("src.services.provision_service.associate_business_terms")
@mock.patch("src.services.provision_service.apply_custom_metadata")
@mock.patch("src.services.provision_service.upsert_data_product")
@mock.patch("src.services.provision_service.ensure_source_system_link")
@mock.patch("src.services.provision_service.upsert_technical_asset")
@mock.patch("src.services.provision_service.ensure_parent_hierarchy")
@mock.patch("src.services.provision_service.OutputPortResolverRegistry")
@mock.patch("src.services.provision_service.ensure_business_term")
@mock.patch("src.services.provision_service.resolve_glossary_name")
@mock.patch("src.services.provision_service.ensure_custom_metadata_def")
def test_provision_data_product_skips_source_system_link_when_info_absent(
    mock_ensure_cmd,
    mock_resolve_glossary,
    mock_ensure_term,
    mock_registry_cls,
    mock_ensure_hierarchy,
    mock_upsert_asset,
    mock_ensure_link,
    mock_upsert_dp,
    mock_apply_cmd,
    mock_associate_terms,
):
    output_port = _build_output_port()  # no `info` (e.g. `/v1/validate`-shaped descriptor)
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings()

    mock_resolve_glossary.return_value = "finance"
    mock_ensure_term.side_effect = lambda client, settings, glossary, fqn: mock.MagicMock(name=f"term-{fqn}")

    catalog_asset = _catalog_asset()
    mock_registry_cls.return_value.resolve.return_value = catalog_asset
    mock_upsert_asset.return_value = AssetUpsertResult(
        asset_guid="view-guid-1", column_guids={"order_id": "col-guid-1"}
    )  # noqa: E501
    mock_upsert_dp.return_value = "dp-guid-1"

    provision_data_product(client, settings, data_product, output_port.id)

    mock_ensure_link.assert_not_called()


@mock.patch("src.services.provision_service.associate_business_terms")
@mock.patch("src.services.provision_service.apply_custom_metadata")
@mock.patch("src.services.provision_service.upsert_data_product")
@mock.patch("src.services.provision_service.OutputPortResolverRegistry")
@mock.patch("src.services.provision_service.ensure_business_term")
@mock.patch("src.services.provision_service.resolve_glossary_name")
@mock.patch("src.services.provision_service.ensure_custom_metadata_def")
def test_provision_data_product_skips_dataproduct_upsert_when_no_supported_output_ports(
    mock_ensure_cmd,
    mock_resolve_glossary,
    mock_ensure_term,
    mock_registry_cls,
    mock_upsert_dp,
    mock_apply_cmd,
    mock_associate_terms,
):
    output_port = _build_output_port(technology="Unsupported")
    data_product = _build_data_product([output_port])
    client = mock.MagicMock()
    settings = _settings()

    mock_resolve_glossary.return_value = "finance"
    mock_registry_cls.return_value.resolve.return_value = None

    result = provision_data_product(client, settings, data_product, output_port.id)

    assert result.status == Status1.COMPLETED
    mock_upsert_dp.assert_not_called()
    assert result.info.publicInfo == {}


@mock.patch("src.services.provision_service.ensure_custom_metadata_def")
def test_provision_data_product_wraps_known_error_with_its_code(mock_ensure_cmd):
    class _FakeError(Exception):
        code = "CUSTOM_METADATA_DEF_FAILED"

    mock_ensure_cmd.side_effect = _FakeError("boom")
    data_product = _build_data_product([_build_output_port()])
    client = mock.MagicMock()
    settings = _settings()

    result = provision_data_product(client, settings, data_product, "some-component")

    assert result.error.startswith("CUSTOM_METADATA_DEF_FAILED:")


@mock.patch("src.services.provision_service.ensure_custom_metadata_def")
def test_provision_data_product_wraps_unexpected_error(mock_ensure_cmd):
    mock_ensure_cmd.side_effect = RuntimeError("boom")
    data_product = _build_data_product([_build_output_port()])
    client = mock.MagicMock()
    settings = _settings()

    result = provision_data_product(client, settings, data_product, "some-component")

    assert result.error.startswith("UNEXPECTED_ERROR:")
