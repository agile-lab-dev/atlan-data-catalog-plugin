from __future__ import annotations

from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Status(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ValidationRequest(BaseModel):
    descriptor: str


class DescriptorKind(StrEnum):
    DATAPRODUCT_DESCRIPTOR = "DATAPRODUCT_DESCRIPTOR"
    COMPONENT_DESCRIPTOR = "COMPONENT_DESCRIPTOR"
    DATAPRODUCT_DESCRIPTOR_WITH_RESULTS = "DATAPRODUCT_DESCRIPTOR_WITH_RESULTS"


class ProvisioningRequest(BaseModel):
    descriptorKind: DescriptorKind
    descriptor: str = Field(
        ...,
        description="Descriptor specification in yaml format. Its structure changes according to `descriptorKind`.",  # noqa: E501
    )
    removeData: Optional[bool] = Field(
        default=None,
        description="If true, when a component is undeployed, its underlying data will also be deleted",
    )  # noqa: E501


class Status1(Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ValidationError(BaseModel):
    errors: List[str]


class ErrorMoreInfo(BaseModel):
    problems: List[str] = Field(
        ...,
        description="Array of possible multiple problems: i.e. multiple validations failed",
    )  # noqa: E501
    solutions: List[str] = Field(
        ...,
        description="Array of possible solutions that the developer gives to the user to solve the issue",
    )  # noqa: E501


class RequestValidationError(BaseModel):
    errors: List[str] = Field(..., deprecated=True)
    userMessage: Optional[str] = Field(default=None, description="User-readable message to be displayed")
    input: Optional[str] = Field(
        default=None,
        description="Optional field to include the file or descriptor that raised the error",
    )  # noqa: E501
    inputErrorField: Optional[str] = Field(
        default=None,
        description="Optional field to include the field path (in dot format) that raised the error",
    )  # noqa: E501
    moreInfo: Optional[ErrorMoreInfo] = None


class ProvisionInfo(BaseModel):
    request: str = Field(
        ...,
        description="Provisioning descriptor of type `COMPONENT_DESCRIPTOR` (see [DescriptorKind](#/components/schemas/DescriptorKind) schema) in JSON format. It had been used to provision the data product component",  # noqa: E501
    )
    result: str = Field(
        ...,
        description="Result message (e.g. a provisiong error or a success message returned by the tech adapter in the [ProvisioningStatus](#/components/schemas/ProvisioningStatus))",  # noqa: E501
    )


class SystemErr(BaseModel):
    error: str


class ReverseProvisioningRequest(BaseModel):
    useCaseTemplateId: str = Field(
        ...,
        description="Component's use case template id",
        examples=["urn:dmb:utm:op-standard:0.0.0"],
    )
    environment: str = Field(..., description="Target environment", examples=["production"])
    params: Optional[Dict] = Field(
        default=None,
        description="Reverse provisioning input params",
        examples=[{"inputA": "value A", "inputB": 1}],
    )
    catalogInfo: Optional[Dict] = Field(
        default=None,
        description="Content of the current `catalog-info.yaml` of the component",
    )


class Info(BaseModel):
    publicInfo: Dict[str, Any] = Field(
        ...,
        description="Fields to display in the Marketplace UI. Note that only the values compliant to specific "
        'structures will be rendered in the "Technical Information" card of the Marketplace pages. '
        "[Check the documentation](https://docs.witboost.com/docs/apis/extension-points/market-plane/data-catalog-plugin-api)"
        "for additional details\n",
    )
    privateInfo: Dict[str, Any] = Field(
        ...,
        description="All the values in this object will be stored in the deployed descriptor, but will not be shown in the Marketplace UI",  # noqa: E501
    )


class UpdateAclRequest(BaseModel):
    refs: List[str] = Field(
        ...,
        description="Identities (i.e. users and groups) involved in the ACL update request",  # noqa: E501
        examples=[
            "user:alice",
            "user:bob",
            "group:groupA",
            "group:groupB",
            "group:groupC",
        ],
    )
    provisionInfo: ProvisionInfo


class ProvisioningStatus(BaseModel):
    status: Status1
    result: str
    info: Optional[Info] = None


class ReverseProvisioningStatus(BaseModel):
    status: Status1
    updates: dict = Field(
        ...,
        description="Field updates to be applied to the componenent's `catalog-info.yaml`. See "
        "the Reverse Provisioning documentation to learn more about the syntax of "
        "this object.",
        examples=[
            {
                "metadata.fieldA": "Value A",
                "spec.mesh.description": "Updated value",
                "spec.fieldB": {"subfieldA": "Value A", "subfieldB": 1},
            }
        ],
    )


class ValidationResult(BaseModel):
    valid: bool
    error: Optional[ValidationError] = None


class ValidationStatus(BaseModel):
    status: Status
    result: Optional[ValidationResult] = None


class EntityReference(BaseModel):
    reference: str = Field(
        ...,
        description="Reference to the Data Catalog entity. Either an ID or a link to the entity in the Data Catalog.",  # noqa: E501
    )
