import os
from unittest import mock

import pytest

from src.models.data_product_descriptor import (
    ComponentKind,
    DataContract,
    DataSharingAgreement,
    OpenMetadataColumn,
    OutputPort,
)
from src.resolvers.base import (
    ConnectionNotConfiguredError,
    IdentityResolutionFailedError,
    ResolverContext,
)
from src.resolvers.databricks import DatabricksOutputPortResolver
from src.resolvers.registry import OutputPortResolverRegistry
from src.settings.atlan_settings import AtlanSettings


def _build_output_port(**overrides) -> OutputPort:
    defaults = dict(
        id="op1",
        name="Output Port 1",
        description="An output port",
        specific={
            "catalogNameOP": "sales_catalog",
            "schemaNameOP": "sales_schema",
            "viewNameOP": "orders_view",
        },
        kind=ComponentKind.OUTPUTPORT,
        version="1.0",
        infrastructureTemplateId="infra1",
        outputPortType="SQL",
        technology="Databricks",
        dependsOn=[],
        dataContract=DataContract(schema=[OpenMetadataColumn(name="order_id", dataType="string")]),
        dataSharingAgreement=DataSharingAgreement(),
        tags=[],
        semanticLinking=[],
    )
    defaults.update(overrides)
    return OutputPort(**defaults)


def _settings(connection_mapping: dict | None = None) -> AtlanSettings:
    # Isolated from any real application.yaml in the working directory (whose
    # `connection_mapping` would otherwise get deep-merged into this test's
    # override by pydantic-settings, polluting "not configured" test scenarios
    # with local/tenant-specific values).
    with mock.patch.dict(os.environ, {"ATLAN_CONFIG_FILE": "/nonexistent/application.yaml"}):
        return AtlanSettings(
            base_url="https://tenant.atlan.com",
            oauth_client_id="id",
            oauth_client_secret="secret",
            connection_mapping=connection_mapping or {},
        )


def test_registry_skips_unsupported_technology():
    registry = OutputPortResolverRegistry()
    output_port = _build_output_port(technology="Snowflake")

    result = registry.resolve(output_port, _settings(), environment="production")

    assert result is None


def test_registry_resolves_databricks_output_port():
    settings = _settings(connection_mapping={"databricks": {"production": "default/databricks/1692345678"}})
    output_port = _build_output_port()
    registry = OutputPortResolverRegistry()

    result = registry.resolve(output_port, settings, environment="production")

    assert result is not None
    assert result.connection_identity.qualified_name == "default/databricks/1692345678"
    assert result.asset_identity.type_name == "View"
    assert (
        result.asset_identity.qualified_name == "default/databricks/1692345678/sales_catalog/sales_schema/orders_view"
    )
    assert result.database_identity.qualified_name == "default/databricks/1692345678/sales_catalog"
    assert result.schema_identity.qualified_name == "default/databricks/1692345678/sales_catalog/sales_schema"
    assert len(result.columns) == 1
    assert result.columns[0].name == "order_id"


def test_registry_matches_technology_case_insensitively():
    settings = _settings(connection_mapping={"DataBricks": {"production": "default/databricks/1692345678"}})
    output_port = _build_output_port(technology="DATABRICKS")
    registry = OutputPortResolverRegistry()

    result = registry.resolve(output_port, settings, environment="production")

    assert result is not None


def test_registry_raises_when_connection_not_configured():
    output_port = _build_output_port()
    registry = OutputPortResolverRegistry()

    with pytest.raises(ConnectionNotConfiguredError):
        registry.resolve(output_port, _settings(), environment="production")


def test_databricks_resolver_raises_on_missing_identity_fields():
    resolver = DatabricksOutputPortResolver()
    output_port = _build_output_port(specific={"catalogNameOP": "sales_catalog"})
    context = ResolverContext(connection_qualified_name="default/databricks/1692345678", environment="production")

    with pytest.raises(IdentityResolutionFailedError):
        resolver.resolve(output_port, context)


def test_databricks_resolver_supports_is_case_insensitive():
    resolver = DatabricksOutputPortResolver()

    assert resolver.supports(_build_output_port(technology="databricks")) is True
    assert resolver.supports(_build_output_port(technology="Databricks")) is True
    assert resolver.supports(_build_output_port(technology="Snowflake")) is False
    assert resolver.supports(_build_output_port(technology=None)) is False


def test_databricks_resolver_preserves_parameterized_odcs_types():
    output_port = _build_output_port(
        dataContract=DataContract(
            **{
                "schema": [
                    {
                        "name": "orders",
                        "properties": [
                            {"name": "id", "physicalType": "INT"},
                            {"name": "label", "physicalType": "VARCHAR(100)"},
                            {"name": "amount", "physicalType": "DECIMAL(10,2)"},
                            {"name": "tags_list", "physicalType": "ARRAY<VARCHAR(50)>"},
                        ],
                    }
                ]
            }
        )
    )
    resolver = DatabricksOutputPortResolver()
    context = ResolverContext(connection_qualified_name="default/databricks/1692345678", environment="production")

    result = resolver.resolve(output_port, context)

    columns_by_name = {column.name: column.data_type for column in result.columns}
    assert columns_by_name["id"] == "INT"
    assert columns_by_name["label"] == "VARCHAR(100)"
    assert columns_by_name["amount"] == "DECIMAL(10,2)"
    assert columns_by_name["tags_list"] == "ARRAY<VARCHAR(50)>"
