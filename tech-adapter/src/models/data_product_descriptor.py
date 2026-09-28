import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Dict, List, Literal, Optional, Type

from loguru import logger
from pydantic import (
    AnyUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.models.constants import OPENMETADATA_SUPPORTED_DATATYPES


class ComponentKind(StrEnum):
    OUTPUTPORT = "outputport"
    WORKLOAD = "workload"
    STORAGE = "storage"
    OBSERVABILITY = "observability"


class CaseInsensitiveEnum(StrEnum):
    @classmethod
    def _missing_(cls, value):
        value = value.lower()
        for member in cls:
            if member.lower() == value:
                return member
        return None


class TagSourceTagLabel(CaseInsensitiveEnum):
    CLASSIFICATION = "Classification"
    GLOSSARY = "Glossary"


class LabelTypeTagLabel(CaseInsensitiveEnum):
    MANUAL = "Manual"
    PROPAGATED = "Propagated"
    AUTOMATED = "Automated"
    DERIVED = "Derived"


class StateTagLabel(CaseInsensitiveEnum):
    SUGGESTED = "Suggested"
    CONFIRMED = "Confirmed"


class OpenMetadataTagLabel(BaseModel):
    tagFQN: str
    description: Optional[str] = None
    source: TagSourceTagLabel = TagSourceTagLabel.CLASSIFICATION
    labelType: LabelTypeTagLabel = LabelTypeTagLabel.MANUAL
    state: StateTagLabel = StateTagLabel.CONFIRMED
    href: Optional[str] = None


class ConnectionTypeWorkload(CaseInsensitiveEnum):
    HOUSEKEEPING = "HOUSEKEEPING"
    DATAPIPELINE = "DATAPIPELINE"


class DataProductCriticalityLabel(CaseInsensitiveEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class DataProductSensitivityLabel(CaseInsensitiveEnum):
    PUBLIC = "Public"
    INTERNAL = "Internal"
    CONFIDENTIAL = "Confidential"


class OpenMetadataColumn(BaseModel):
    name: str
    dataType: str
    dataLength: Optional[int] = None
    arrayDataType: Optional[str] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    description: Optional[str] = None
    tags: Optional[List[OpenMetadataTagLabel]] = None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: Any) -> Any:
        """Accept ODCS string tags as well as legacy tag objects."""
        if not isinstance(value, list):
            return value
        return [
            {"tagFQN": tag, "source": TagSourceTagLabel.GLOSSARY} if isinstance(tag, str) else tag
            for tag in value
        ]

    @field_validator("dataType")
    @classmethod
    def check_dataType(cls, value, values):
        if value.upper() not in OPENMETADATA_SUPPORTED_DATATYPES:
            data = values.data if hasattr(values, "data") else values
            column_name = data.get("name", "<unknown>")
            raise ValueError(
                'Column "'
                + column_name
                + '" specifies dataType of "'
                + value
                + '" but this is not a valid OpenMetadata data type'
            )
        return value

    @field_validator("arrayDataType")
    @classmethod
    def check_array_data_type(cls, value: Optional[str]) -> Optional[str]:
        """Recursively normalize and render a parameterized ARRAY element type (e.g. `ARRAY<VARCHAR(50)>`)."""  # noqa: E501
        if value is None:
            return None
        try:
            nested_column = cls(name="<array item>", **_normalize_physical_type(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f'Array specifies invalid element type "{value}"') from error
        return nested_column.databricks_data_type()

    def databricks_data_type(self) -> str:
        """
        Renders this column's type using Databricks SQL syntax, reconstructing the
        full parameterized type (`VARCHAR(n)`, `DECIMAL(p,s)`, `ARRAY<...>`) from
        `dataType` plus its length/precision/scale/array-element fields. Used
        wherever the exact physical type — not just the bare `dataType` — must reach
        Atlan (see `resolvers/databricks.py`), since `dataType` alone is
        deliberately kept as the bare OpenMetadata-supported type for validation
        (`check_dataType` above).
        """  # noqa: E501
        data_type = self.dataType.upper()
        if data_type == "ARRAY" and self.arrayDataType:
            return f"ARRAY<{self.arrayDataType}>"
        if data_type in {"CHAR", "VARCHAR"} and self.dataLength is not None:
            return f"{data_type}({self.dataLength})"
        if data_type in {"DECIMAL", "NUMERIC"} and self.precision is not None:
            scale = self.scale if self.scale is not None else 0
            return f"{data_type}({self.precision},{scale})"
        return self.dataType


class DataContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_: Optional[List[OpenMetadataColumn]] = Field(..., alias="schema")

    @model_validator(mode="before")
    @classmethod
    def normalize_odcs_schema(cls, value: Any) -> Any:
        """Flatten ODCS datasets and properties into the legacy column model."""
        if not isinstance(value, dict) or not isinstance(value.get("schema"), list):
            return value

        normalized: list[dict[str, Any]] = []
        for dataset in value["schema"]:
            if not isinstance(dataset, dict) or "properties" not in dataset:
                normalized.append(dataset)
                continue

            for prop in dataset.get("properties") or []:
                if not isinstance(prop, dict):
                    continue

                physical_type = prop.get("physicalType") or prop.get("logicalType")
                normalized.append(
                    {
                        "name": prop.get("name"),
                        **_normalize_physical_type(physical_type),
                        "description": prop.get("description"),
                        "tags": prop.get("tags", []),
                    }
                )

        return {**value, "schema": normalized}


def _normalize_physical_type(physical_type: Any) -> Dict[str, Any]:
    """Convert an ODCS physical type into legacy column fields."""
    if not isinstance(physical_type, str):
        return {"dataType": physical_type}

    value = physical_type.strip()
    array_match = re.fullmatch(r"ARRAY\s*<\s*(.+)\s*>", value, flags=re.IGNORECASE)
    if array_match:
        return {"dataType": "ARRAY", "arrayDataType": array_match.group(1).strip()}

    length_match = re.fullmatch(r"(VARCHAR|CHAR)\s*\(\s*(\d+)\s*\)", value, flags=re.IGNORECASE)
    if length_match:
        return {"dataType": length_match.group(1).upper(), "dataLength": int(length_match.group(2))}

    decimal_match = re.fullmatch(
        r"(DECIMAL|NUMERIC)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)",
        value,
        flags=re.IGNORECASE,
    )
    if decimal_match:
        return {
            "dataType": decimal_match.group(1).upper(),
            "precision": int(decimal_match.group(2)),
            "scale": int(decimal_match.group(3)),
        }

    return {"dataType": value}


class DataSharingAgreement(BaseModel):
    purpose: Optional[str] = None
    billing: Optional[str] = None
    security: Optional[str] = None
    intendedUsage: Optional[str] = None
    limitations: Optional[str] = None
    lifeCycle: Optional[str] = None
    confidentiality: Optional[str] = None


class Component(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: ComponentKind

    id: str
    name: str
    fullyQualifiedName: Optional[str] = None
    description: str
    specific: dict


class ComponentInfo(BaseModel):
    """
    `info` enrichment attached to a component only on `provision`/`unprovision`
    (`DATAPRODUCT_DESCRIPTOR_WITH_RESULTS`), never on `validate`
    (`DATAPRODUCT_DESCRIPTOR`) — see docs/technical-details.md §Provisioning.
    Kept permissive (`extra="allow"`, untyped `publicInfo`/`privateInfo` values)
    since the plugin only ever reads the single `tableUrl.href` key it needs.
    """  # noqa: E501

    model_config = ConfigDict(extra="allow")
    publicInfo: Dict[str, Any] = Field(default_factory=dict)
    privateInfo: Dict[str, Any] = Field(default_factory=dict)


class OutputPort(Component):
    model_config = ConfigDict(extra="allow")
    kind: Literal[ComponentKind.OUTPUTPORT]

    version: str
    infrastructureTemplateId: str
    useCaseTemplateId: Optional[str] = None
    dependsOn: List[str]
    platform: Optional[str] = None
    technology: Optional[str] = None
    outputPortType: str
    creationDate: Optional[datetime] = None
    startDate: Optional[datetime] = None
    retentionTime: Optional[str] = None
    processDescription: Optional[str] = None
    dataContract: DataContract
    dataSharingAgreement: Optional[DataSharingAgreement] = None
    tags: List[OpenMetadataTagLabel]
    sampleData: Optional[dict] = None  # OpenMetadataTable
    semanticLinking: List[dict]
    info: Optional[ComponentInfo] = None

    @field_validator("kind")
    @classmethod
    def check_kind(cls, value, values):
        if value == ComponentKind.OUTPUTPORT:
            return value
        else:
            raise ValueError(
                f"kind of component with id {values.get('id')} must be 'outputport'"  # noqa: E501
            )

    def source_table_url(self) -> Optional[str]:
        """
        Best-effort `info.publicInfo.tableUrl.href` deep link to the underlying
        technical asset in its source technology (e.g. the Databricks table/view
        explorer URL). `None` when `info` is absent (e.g. on `/v1/validate`, or
        when the enriching Tech Adapter did not populate it) — callers must treat
        this as optional, never fail provisioning because of it.
        """  # noqa: E501
        if self.info is None:
            return None
        table_url = self.info.publicInfo.get("tableUrl")
        if not isinstance(table_url, dict):
            return None
        href = table_url.get("href")
        return href if isinstance(href, str) and href else None


class Workload(Component):
    model_config = ConfigDict(extra="allow")
    kind: Literal[ComponentKind.WORKLOAD]

    version: str
    infrastructureTemplateId: str
    useCaseTemplateId: Optional[str] = None
    dependsOn: List[str]
    platform: Optional[str] = None
    technology: Optional[str] = None
    workloadType: Optional[str] = None
    connectionType: ConnectionTypeWorkload
    tags: List[OpenMetadataTagLabel]
    readsFrom: Optional[List[str]] = None

    @field_validator("kind")
    @classmethod
    def check_kind(cls, value, values):
        if value == ComponentKind.WORKLOAD:
            return value
        else:
            raise ValueError(
                f"kind of component with id {values.get('id')} must be 'workload'"  # noqa: E501
            )


class StorageArea(Component):
    model_config = ConfigDict(extra="allow")
    kind: Literal[ComponentKind.STORAGE]

    infrastructureTemplateId: str
    useCaseTemplateId: Optional[str] = None
    dependsOn: List[str]
    platform: Optional[str] = None
    technology: Optional[str] = None
    storageType: Optional[str] = None
    tags: List[OpenMetadataTagLabel]

    @field_validator("kind")
    @classmethod
    def check_kind(cls, value, values):
        if value == ComponentKind.STORAGE:
            return value
        else:
            raise ValueError(
                f"kind of component with id {values.get('id')} must be 'storage'"  # noqa: E501
            )


class Observability(Component):
    model_config = ConfigDict(extra="allow")
    kind: Literal[ComponentKind.OBSERVABILITY]

    endpoint: AnyUrl
    completeness: dict
    dataProfiling: dict
    freshness: dict
    availability: dict
    dataQuality: dict

    @field_validator("kind")
    @classmethod
    def check_kind(cls, value, values):
        if value == ComponentKind.OBSERVABILITY:
            return value
        else:
            raise ValueError(
                f"kind of component with id {values.get('id')} must be 'observability'"  # noqa: E501
            )


component_map: dict[str, Type[Component]] = {
    ComponentKind.OBSERVABILITY: Observability,
    ComponentKind.OUTPUTPORT: OutputPort,
    ComponentKind.WORKLOAD: Workload,
    ComponentKind.STORAGE: StorageArea,
}


def parse_component(data: dict | Component) -> Component:
    if isinstance(data, Component):  # happens if DP is created in code
        if data.kind not in component_map:
            raise ValueError(f"Unknown component kind: {data.kind}")
        return data
    else:
        kind = data.get("kind")
        if kind not in component_map:
            raise ValueError(f"Unknown component kind: {kind}")
        component = component_map[kind](**data)
        logger.debug("Parsed component: " + str(component))
        return component


class DataProduct(BaseModel):
    id: str
    name: str
    fullyQualifiedName: Optional[str] = None
    description: str
    kind: Literal["dataproduct"]
    domain: str
    version: str
    environment: str
    dataProductOwner: str
    dataProductOwnerDisplayName: Optional[str] = None
    email: Optional[str] = None
    ownerGroup: str
    devGroup: str
    informationSLA: Optional[str] = None
    status: Optional[str] = None
    maturity: Optional[str] = None
    billing: Optional[dict] = None
    criticality: Optional[DataProductCriticalityLabel] = None
    sensitivity: Optional[DataProductSensitivityLabel] = None
    tags: List[OpenMetadataTagLabel]
    specific: dict
    components: List[Annotated[Component, BeforeValidator(parse_component)]]

    def get_components_by_kind(self, kind: str) -> List[Component]:
        """
        Filters the components associated with the data product and returns
        a list containing only the components that have the specified kind.

        Args:
            kind (str): The kind of components to retrieve.

        Returns:
            List[Component]: A list of Component objects that match the specified kind.
            If no matching components are found, an empty list is returned.

        Example:
            To retrieve all components of kind 'outputport' from a data product 'my_data_product':
            >>> outputport_components = my_data_product.get_components_by_kind('outputport')
        """  # noqa: E501

        new_components_list = [component for component in self.components if component.kind == kind]

        return new_components_list

    def get_component_by_id(self, component_id: str) -> Component | None:
        """
        Retrieve a component within the data product by its unique identifier.

        This method searches for a component with the specified ID within the data product's
        list of components and returns the matching component, if found.

        Args:
            component_id (str): The unique identifier of the component to retrieve.

        Returns:
            Component | None: The Component object with the specified ID if found, or None if
            no matching component is found.

        Example:
           To retrieve a specific component with ID '12345' from a data product 'my_data_product':
           >>> specific_component = my_data_product.get_component_by_id('12345')
           >>> if specific_component:
           ...     print(f"Found component: {specific_component.name}")
           ... else:
           ...     print("Component not found.")
        """  # noqa: E501
        for component in self.components:
            if component.id == component_id:
                return component
        return None

    def get_typed_component_by_id(self, component_id: str, component_type: Type[BaseModel]):
        component = self.get_component_by_id(component_id)
        if component is not None:
            return component_type.model_validate(component.model_dump(by_alias=True))
        else:
            return None

    def get_output_ports(self) -> List[OutputPort]:
        """
        Retrieve a list of output ports associated with the data product.

        This method filters the components of kind 'outputport' that are associated with
        the data product and returns a list containing these output ports.

        Returns:
            List[OutputPort]: A list of OutputPort objects that represent the output
            ports associated with the data product.

        Example:
            To retrieve all output ports from a data product 'my_data_product':
            >>> output_ports = my_data_product.get_output_ports()
        """  # noqa: E501

        output_ports: List[OutputPort] = []
        for op in self.get_components_by_kind("outputport"):
            if type(op) is OutputPort:
                output_ports.append(op)
        return output_ports

    def get_workloads(self) -> List[Workload]:
        """
        Retrieve a list of workloads associated with the data product.

        This method filters the components of kind 'workload' that are associated with
        the data product and returns a list containing these workloads.

        Returns:
            List[Workload]: A list of Workload objects that represent the workloads
            associated with the data product.

        Example:
            To retrieve all workloads from a data product 'my_data_product':
            >>> workloads = my_data_product.get_workloads()
        """  # noqa: E501
        workloads: List[Workload] = []
        for wl in self.get_components_by_kind("workload"):
            if type(wl) is Workload:
                workloads.append(wl)
        return workloads

    def get_storage_areas(self) -> List[StorageArea]:
        """
        Retrieve a list of storage areas associated with the data product.

        This method filters the components of kind 'storage' that are associated with
        the data product and returns a list containing these storage areas.

        Returns:
            List[StorageArea]: A list of StorageArea objects that represent the storage
            areas associated with the data product.

        Example:
            To retrieve all storage areas from a data product 'my_data_product':
            >>> storage_areas = my_data_product.get_storage_areas()
        """  # noqa: E501
        storage_areas: List[StorageArea] = []
        for st in self.get_components_by_kind("storage"):
            if type(st) is StorageArea:
                storage_areas.append(st)
        return storage_areas

    def get_observability_APIs(self) -> List[Observability]:
        """
        Retrieve a list of observability APIs associated with the data product.

        This method filters the components of kind 'observability' that are associated with
        the data product and returns a list containing these observability APIs.

        Returns:
            List[Observability]: A list of Observability objects that represent the observability
            APIs associated with the data product.

        Example:
            To retrieve all observability APIs from a data product 'my_data_product':
            >>> observability_apis = my_data_product.get_observability_APIs()
        """  # noqa: E501
        observability_APIs: List[Observability] = []
        for obs in self.get_components_by_kind("observability"):
            if type(obs) is Observability:
                observability_APIs.append(obs)
        return observability_APIs
