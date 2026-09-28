import unittest
from pathlib import Path

import pydantic_core
import pytest
import yaml

from src.models.data_product_descriptor import (
    ComponentKind,
    ConnectionTypeWorkload,
    DataContract,
    DataProduct,
    DataProductCriticalityLabel,
    DataProductSensitivityLabel,
    DataSharingAgreement,
    Observability,
    OpenMetadataColumn,
    OutputPort,
    StorageArea,
    TagSourceTagLabel,
    Workload,
)
from src.utility.parsing_pydantic_models import parse_yaml_with_model


class TestDataProductDescriptor(unittest.TestCase):
    def setUp(self):
        # Create sample OpenMetadataColumn instances for the schema
        column1 = OpenMetadataColumn(name="column1", dataType="string")
        column2 = OpenMetadataColumn(name="column2", dataType="int")

        # Create a sample DataProduct object for testing
        self.sample_data_product = DataProduct(
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
            components=[
                OutputPort(
                    id="op1",
                    name="Output Port 1",
                    description="An output port",
                    specific={},
                    kind=ComponentKind.OUTPUTPORT,
                    version="1.0",
                    infrastructureTemplateId="infra1",
                    outputPortType="Type1",
                    dependsOn=["op2"],  # Depends on another output port within the same Data Product
                    dataContract=DataContract(schema=[column1, column2]),  # Provide valid schema instances
                    dataSharingAgreement=DataSharingAgreement(),
                    tags=[],
                    semanticLinking=[],
                ),
                OutputPort(
                    id="op2",
                    name="Output Port 2",
                    description="Another output port",
                    specific={},
                    kind=ComponentKind.OUTPUTPORT,
                    version="1.0",
                    infrastructureTemplateId="infra2",
                    outputPortType="Type2",
                    dependsOn=[],  # No dependencies
                    dataContract=DataContract(schema=[]),
                    dataSharingAgreement=DataSharingAgreement(),
                    tags=[],
                    semanticLinking=[],
                ),
                Workload(
                    id="wl1",
                    name="Workload 1",
                    description="A workload",
                    specific={},
                    kind=ComponentKind.WORKLOAD,
                    version="1.0",
                    infrastructureTemplateId="infra2",
                    connectionType=ConnectionTypeWorkload.HOUSEKEEPING,
                    dependsOn=[],
                    tags=[],
                ),
                StorageArea(
                    id="sa1",
                    name="Storage Area 1",
                    description="A storage area",
                    specific={},
                    kind=ComponentKind.STORAGE,
                    owners=["Owner1"],
                    infrastructureTemplateId="infra3",
                    dependsOn=[],
                    tags=[],
                ),
                Observability(
                    id="obs1",
                    name="Observability 1",
                    description="An observability component",
                    specific={},
                    kind=ComponentKind.OBSERVABILITY,
                    endpoint="http://example.com",
                    completeness={},
                    dataProfiling={},
                    freshness={},
                    availability={},
                    dataQuality={},
                    tags=[],
                ),
            ],
            tags=[],
        )

    def test_get_components_by_kind_outputport(self):
        output_ports = self.sample_data_product.get_components_by_kind(ComponentKind.OUTPUTPORT)
        self.assertEqual(2, len(output_ports))
        self.assertIsInstance(output_ports[0], OutputPort)

    def test_get_components_by_kind_workload(self):
        workloads = self.sample_data_product.get_components_by_kind(ComponentKind.WORKLOAD)
        self.assertEqual(1, len(workloads))
        self.assertIsInstance(workloads[0], Workload)

    def test_get_components_by_kind_storage(self):
        storage_areas = self.sample_data_product.get_components_by_kind(ComponentKind.STORAGE)
        self.assertEqual(1, len(storage_areas))
        self.assertIsInstance(storage_areas[0], StorageArea)

    def test_get_components_by_kind_observability(self):
        observability_apis = self.sample_data_product.get_components_by_kind(ComponentKind.OBSERVABILITY)
        self.assertEqual(1, len(observability_apis))
        self.assertIsInstance(observability_apis[0], Observability)

    def test_get_component_by_id_existing(self):
        component_id = "op1"
        component = self.sample_data_product.get_component_by_id(component_id)
        self.assertIsNotNone(component)
        self.assertEqual(component.id, component_id)

    def test_get_component_by_id_non_existing(self):
        component_id = "nonexistent"
        component = self.sample_data_product.get_component_by_id(component_id)
        self.assertIsNone(component)

    def test_get_components_by_kind_outputport_with_dependencies(self):
        output_ports = self.sample_data_product.get_components_by_kind(ComponentKind.OUTPUTPORT)
        self.assertEqual(2, len(output_ports))
        self.assertIsInstance(output_ports[0], OutputPort)
        self.assertIsInstance(output_ports[1], OutputPort)
        self.assertIn("op1", [op.id for op in output_ports])
        self.assertIn("op2", [op.id for op in output_ports])

    def test_get_output_ports(self):
        output_ports = self.sample_data_product.get_output_ports()
        self.assertEqual(2, len(output_ports))
        self.assertIsInstance(output_ports[0], OutputPort)
        self.assertIn("op1", [op.id for op in output_ports])
        self.assertIn("op2", [op.id for op in output_ports])

    def test_get_workloads(self):
        workloads = self.sample_data_product.get_workloads()
        self.assertEqual(1, len(workloads))
        self.assertIsInstance(workloads[0], Workload)
        self.assertEqual("wl1", workloads[0].id)

    def test_get_storage_areas(self):
        storage_areas = self.sample_data_product.get_storage_areas()
        self.assertEqual(1, len(storage_areas))
        self.assertIsInstance(storage_areas[0], StorageArea)
        self.assertEqual("sa1", storage_areas[0].id)

    def test_get_observability_APIs(self):
        observability_apis = self.sample_data_product.get_observability_APIs()
        self.assertEqual(1, len(observability_apis))
        self.assertIsInstance(observability_apis[0], Observability)
        self.assertEqual("obs1", observability_apis[0].id)

    def test_output_port_check_kind_classmethod(self):
        valid_output_port_data = """
            id: output_port_1
            name: Output Port 1
            description: Description for Output Port 1
            specific: {}
            kind: outputport
        """

        valid_output_port_data = yaml.safe_load(valid_output_port_data)

        OutputPort.check_kind(ComponentKind.OUTPUTPORT, valid_output_port_data)  # Should not raise an error

        invalid_output_port_data = """
            id: output_port_2
            name: Output Port 2
            description: Description for Output Port 2
            specific: {}
            kind: workload  # Invalid kind
        """

        invalid_output_port_data = yaml.safe_load(invalid_output_port_data)
        with pytest.raises(ValueError):
            OutputPort.check_kind(ComponentKind.WORKLOAD, invalid_output_port_data)

    def test_workload_check_kind_classmethod(self):
        valid_workload_data = """
          id: workload_1
          name: Workload 1
          description: Description for Workload 1
          specific: {}
          kind: workload
        """

        valid_workload_data = yaml.safe_load(valid_workload_data)
        Workload.check_kind(ComponentKind.WORKLOAD, valid_workload_data)  # Should not raise an error

        invalid_workload_data = """
          id: workload_2
          name: Workload 2
          description: Description for Workload 2
          specific: {}
          kind: outputport  # Invalid kind
        """

        invalid_workload_data = yaml.safe_load(invalid_workload_data)
        with pytest.raises(ValueError):
            Workload.check_kind(ComponentKind.OUTPUTPORT, invalid_workload_data)

    def test_storage_area_check_kind_classmethod(self):
        valid_storage_area_data = """
              id: storage_1
              name: Storage 1
              description: Description for Storage 1
              specific: {}
              kind: STORAGE
        """

        valid_storage_area_data = yaml.safe_load(valid_storage_area_data)
        StorageArea.check_kind(ComponentKind.STORAGE, valid_storage_area_data)  # Should not raise an error

        invalid_storage_area_data = """
            id: storage_2
            name: Storage 2
            description: Description for Storage 2
            specific: {}
            kind: WORKLOAD  # Invalid kind

        """

        invalid_storage_area_data = yaml.safe_load(invalid_storage_area_data)
        with pytest.raises(ValueError):
            StorageArea.check_kind(ComponentKind.WORKLOAD, invalid_storage_area_data)

    def test_observability_check_kind_classmethod(self):
        valid_observability_data = """
        id: observability_1
        name: Observability 1
        description: Description for Observability 1
        specific: {}
        kind: OBSERVABILITY
        """

        valid_observability_data = yaml.safe_load(valid_observability_data)
        Observability.check_kind(ComponentKind.OBSERVABILITY, valid_observability_data)  # Should not raise an error

        invalid_observability_data = """
        id: observability_2
        name: Observability 2
        description: Description for Observability 2
        specific: {}
        kind: WORKLOAD  # Invalid kind
        """

        invalid_observability_data = yaml.safe_load(invalid_observability_data)

        with pytest.raises(ValueError):
            Observability.check_kind(ComponentKind.WORKLOAD, invalid_observability_data)

    def test_open_metadata_column_check_dataType_classmethod(self):
        valid_column_data = """
        name: column1
        dataType: string
        """

        valid_column_data = yaml.safe_load(valid_column_data)

        OpenMetadataColumn.check_dataType("string", valid_column_data)  # Should not raise an error

        invalid_column_data = """
        name: column2
        dataType: invalid_type  # Invalid dataType

        """

        invalid_column_data = yaml.safe_load(invalid_column_data)

        with pytest.raises(ValueError):
            OpenMetadataColumn.check_dataType("invalid_type", invalid_column_data)

    def test_workload_without_readsFrom(self):
        input_data = """
          id: "123"
          name: "Workload1"
          fullyQualifiedName: "example.Workload1"
          description: "Description"
          specific:
            version: "1.0"
          kind: "workload"
          version: "1.0"
          infrastructureTemplateId: "template1"
          dependsOn:
            - "dependency1"
            - "dependency2"
          connectionType: "DATAPIPELINE"
          tags: []
        """

        result = parse_yaml_with_model(input_data, Workload)
        assert isinstance(result, Workload)

    def test_get_typed_component_output_port(self):
        descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()
        request = yaml.safe_load(descriptor_str)
        data_product = parse_yaml_with_model(request, DataProduct)
        component_to_provision = "urn:dmb:cmp:healthcare:vaccinations:0:snowflake-output-port"

        assert data_product.get_typed_component_by_id(component_to_provision, OutputPort) is not None

        descriptor_str = Path("tests/descriptors/descriptor_storage_valid.yaml").read_text()
        request = yaml.safe_load(descriptor_str)
        data_product = parse_yaml_with_model(request, DataProduct)
        invalid_component_to_provision = "urn:dmb:cmp:healthcare:vaccinations:0:snowflake-storage"

        with pytest.raises(pydantic_core.ValidationError, match="4 validation errors for OutputPort"):
            data_product.get_typed_component_by_id(invalid_component_to_provision, OutputPort)

    def test_criticality_and_sensitivity_default_to_none(self):
        self.assertIsNone(self.sample_data_product.criticality)
        self.assertIsNone(self.sample_data_product.sensitivity)

    def _minimal_data_product_kwargs(self, **overrides):
        defaults = dict(
            id="urn:dmb:dp:finance:risk-finance:0",
            name="Risk Finance",
            description="A test data product",
            kind="dataproduct",
            domain="finance",
            version="0.1.0",
            environment="production",
            dataProductOwner="user:jane.doe_agilelab.it",
            ownerGroup="group:finance-owners",
            devGroup="group:finance-dev",
            specific={},
            components=[],
            tags=[],
        )
        defaults.update(overrides)
        return defaults

    def test_criticality_and_sensitivity_parse_case_insensitively(self):
        kwargs = self._minimal_data_product_kwargs(criticality="high", sensitivity="confidential")
        data_product = DataProduct(**kwargs)

        self.assertEqual(DataProductCriticalityLabel.HIGH, data_product.criticality)
        self.assertEqual(DataProductSensitivityLabel.CONFIDENTIAL, data_product.sensitivity)

    def test_criticality_rejects_unknown_value(self):
        with pytest.raises(pydantic_core.ValidationError):
            DataProduct(**self._minimal_data_product_kwargs(criticality="extreme"))


def test_odcs_schema_is_flattened_to_columns_and_string_tags_are_normalized():
    data_contract = DataContract(
        **{
            "schema": [
                {
                    "name": "orders",
                    "physicalType": "view",
                    "logicalType": "object",
                    "properties": [
                        {
                            "name": "order_id",
                            "physicalType": "INT",
                            "logicalType": "integer",
                            "description": "Order identifier",
                            "tags": ["identifier"],
                        },
                        {
                            "name": "amount",
                            "physicalType": "DECIMAL(10, 2)",
                            "logicalType": "number",
                            "tags": [],
                        },
                    ],
                }
            ]
        }
    )

    assert data_contract.schema_ is not None
    assert [column.name for column in data_contract.schema_] == ["order_id", "amount"]
    assert data_contract.schema_[0].dataType == "INT"
    assert data_contract.schema_[0].tags is not None
    assert data_contract.schema_[0].tags[0].tagFQN == "identifier"
    assert data_contract.schema_[0].tags[0].source == TagSourceTagLabel.GLOSSARY
    assert data_contract.schema_[1].dataType == "DECIMAL"
    assert data_contract.schema_[1].precision == 10
    assert data_contract.schema_[1].scale == 2


def test_legacy_schema_remains_supported():
    data_contract = DataContract(
        **{
            "schema": [
                {
                    "name": "order_id",
                    "dataType": "INT",
                    "tags": [{"tagFQN": "identifier"}],
                }
            ]
        }
    )

    assert data_contract.schema_ is not None
    assert len(data_contract.schema_) == 1
    assert data_contract.schema_[0].name == "order_id"
    assert data_contract.schema_[0].dataType == "INT"


def test_odcs_descriptor_fixture_parses():
    descriptor_path = Path(__file__).parent / "fixtures" / "sample_descriptor.yaml"
    descriptor = yaml.safe_load(descriptor_path.read_text())

    data_product = parse_yaml_with_model(descriptor, DataProduct)

    assert isinstance(data_product, DataProduct)
    output_port = data_product.get_output_ports()[0]
    assert output_port.dataContract.schema_ is not None
    assert [column.name for column in output_port.dataContract.schema_] == ["id", "name"]
    assert output_port.dataContract.schema_[0].dataType == "INT"
    assert output_port.dataContract.schema_[0].tags is not None
    assert [tag.tagFQN for tag in output_port.dataContract.schema_[0].tags] == ["bt1", "bt2"]
    assert output_port.source_table_url() == (
        "https://adb-1234567890123456.1.azuredatabricks.net/explore/data/"
        "sample-catalog-development/sales_orders-0-output-port-orders/orders_view"
    )


def _output_port_kwargs(**overrides):
    defaults = dict(
        id="op1",
        name="Output Port 1",
        description="An output port",
        specific={},
        kind=ComponentKind.OUTPUTPORT,
        version="1.0",
        infrastructureTemplateId="infra1",
        outputPortType="Type1",
        dependsOn=[],
        dataContract=DataContract(schema=[]),
        dataSharingAgreement=DataSharingAgreement(),
        tags=[],
        semanticLinking=[],
    )
    defaults.update(overrides)
    return defaults


def test_source_table_url_reads_info_public_info_table_url_href():
    href = "https://adb-123.azuredatabricks.net/explore/data/catalog/schema/view"
    output_port = OutputPort(
        **_output_port_kwargs(
            info={
                "publicInfo": {"tableUrl": {"type": "string", "label": "Table URL", "value": "Open", "href": href}},
                "privateInfo": {},
            }
        )
    )

    assert output_port.source_table_url() == href


def test_source_table_url_is_none_when_info_absent():
    output_port = OutputPort(**_output_port_kwargs())

    assert output_port.source_table_url() is None


def test_source_table_url_is_none_when_table_url_missing():
    output_port = OutputPort(**_output_port_kwargs(info={"publicInfo": {}, "privateInfo": {}}))

    assert output_port.source_table_url() is None
