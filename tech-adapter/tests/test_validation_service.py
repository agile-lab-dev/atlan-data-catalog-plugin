from pathlib import Path

import yaml

from src.models.api_models import ValidationError
from src.models.data_product_descriptor import (
    ComponentKind,
    DataContract,
    DataProduct,
    DataSharingAgreement,
    OpenMetadataColumn,
    OutputPort,
)
from src.services.validation_service import validate_data_product
from src.utility.parsing_pydantic_models import parse_yaml_with_model


def _build_output_port(**overrides) -> OutputPort:
    defaults = dict(
        id="op1",
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
        dataContract=DataContract(schema=[OpenMetadataColumn(name="col1", dataType="string")]),
        dataSharingAgreement=DataSharingAgreement(),
        tags=[],
        semanticLinking=[],
    )
    defaults.update(overrides)
    return OutputPort(**defaults)


def _build_data_product(components, **overrides) -> DataProduct:
    defaults = dict(
        id="1",
        name="Sample Data Product",
        description="A test data product",
        kind="dataproduct",
        domain="Sample Domain",
        version="1.0",
        environment="Development",
        dataProductOwner="John Doe",
        ownerGroup="Data Owners",
        devGroup="Development Team",
        specific={},
        components=components,
        tags=[],
    )
    defaults.update(overrides)
    return DataProduct(**defaults)


def test_valid_data_product_passes_validation():
    data_product = _build_data_product([_build_output_port()])

    result = validate_data_product(data_product)

    assert result.valid is True
    assert result.error is None


def test_missing_dp_name_fails_validation():
    data_product = _build_data_product([_build_output_port()], name=" ")

    result = validate_data_product(data_product)

    assert result.valid is False
    assert isinstance(result.error, ValidationError)
    assert any("VALIDATION_MISSING_DP_NAME" in e for e in result.error.errors)


def test_missing_domain_fails_validation():
    data_product = _build_data_product([_build_output_port()], domain="")

    result = validate_data_product(data_product)

    assert result.valid is False
    assert any("VALIDATION_MISSING_DOMAIN" in e for e in result.error.errors)


def test_no_output_ports_fails_validation():
    data_product = _build_data_product([])

    result = validate_data_product(data_product)

    assert result.valid is False
    assert any("VALIDATION_NO_OUTPUT_PORTS" in e for e in result.error.errors)


def test_unsupported_technology_is_skipped_not_reported_as_error():
    data_product = _build_data_product([_build_output_port(technology="Snowflake")])

    result = validate_data_product(data_product)

    assert result.valid is True
    assert result.error is None


def test_missing_technology_is_skipped_not_reported_as_error():
    data_product = _build_data_product([_build_output_port(technology=None)])

    result = validate_data_product(data_product)

    assert result.valid is True
    assert result.error is None


def test_mixed_supported_and_unsupported_output_ports_only_validates_supported_ones():
    unsupported_op = _build_output_port(id="op-snowflake", technology="Snowflake")
    supported_op_with_error = _build_output_port(id="op-databricks", specific={"catalogNameOP": "risk-finance"})
    data_product = _build_data_product([unsupported_op, supported_op_with_error])

    result = validate_data_product(data_product)

    assert result.valid is False
    codes = "\n".join(result.error.errors)
    assert "op-databricks" in codes
    assert "op-snowflake" not in codes


def test_databricks_incomplete_metadata_fails_validation():
    data_product = _build_data_product([_build_output_port(specific={"catalogNameOP": "risk-finance"})])

    result = validate_data_product(data_product)

    assert result.valid is False
    assert any("VALIDATION_INCOMPLETE_METADATA" in e for e in result.error.errors)


def test_missing_schema_fails_validation():
    data_product = _build_data_product([_build_output_port(dataContract=DataContract(schema=[]))])

    result = validate_data_product(data_product)

    assert result.valid is False
    assert any("VALIDATION_MISSING_SCHEMA" in e for e in result.error.errors)


def test_multiple_failures_are_all_reported():
    data_product = _build_data_product([], name="", domain="")

    result = validate_data_product(data_product)

    assert result.valid is False
    codes = "\n".join(result.error.errors)
    assert "VALIDATION_MISSING_DP_NAME" in codes
    assert "VALIDATION_MISSING_DOMAIN" in codes
    assert "VALIDATION_NO_OUTPUT_PORTS" in codes


def test_real_databricks_descriptor_fixture_passes_validation():
    descriptor_str = Path("tests/descriptors/descriptor_databricks_valid.yaml").read_text()
    request = yaml.safe_load(descriptor_str)
    data_product = parse_yaml_with_model(request, DataProduct)

    result = validate_data_product(data_product)

    assert result.valid is True
    assert result.error is None
