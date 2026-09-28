from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Query, Request
from loguru import logger
from pyatlan.client.atlan import AtlanClient
from starlette.background import BackgroundTask
from starlette.responses import Response

from src.app_config import app
from src.check_return_type import check_response
from src.clients.atlan_client_factory import get_atlan_client, get_atlan_settings
from src.dependencies import (
    UnpackedProvisioningRequestDep,
    UnpackedUnprovisioningRequestDep,
    UnpackedUpdateAclRequestDep,
    UnpackedValidationRequestDep,
)
from src.models.api_models import (
    EntityReference,
    ProvisioningStatus,
    Status1,
    SystemErr,
    ValidationError,
    ValidationRequest,
    ValidationResult,
    ValidationStatus,
)
from src.services.entity_reference_service import get_entity_reference
from src.services.provision_service import provision_data_product
from src.services.unprovision_service import unprovision_data_product
from src.services.validation_service import validate_data_product
from src.settings.atlan_settings import AtlanSettings

AtlanClientDep = Annotated[AtlanClient, Depends(get_atlan_client)]
AtlanSettingsDep = Annotated[AtlanSettings, Depends(get_atlan_settings)]


def log_info(req_body, res_code, res_body):
    id = str(uuid.uuid4())
    logger.info("[{}] REQUEST: {}", id, req_body.decode("utf-8"))
    logger.info("[{}] RESPONSE({}): {}", id, res_code, res_body.decode("utf-8"))


@app.middleware("http")
async def log_request_response_middleware(request: Request, call_next):
    req_body = await request.body()
    response = await call_next(request)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    res_body = b"".join(chunks)
    task = BackgroundTask(log_info, req_body, response.status_code, res_body)
    return Response(
        content=res_body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
        background=task,
    )


@app.post(
    "/v1/provision",
    response_model=None,
    responses={
        "200": {"model": ProvisioningStatus},
        "202": {"model": str},
        "400": {"model": ValidationError},
        "500": {"model": SystemErr},
    },
    tags=["TechAdapter"],
)
def provision(request: UnpackedProvisioningRequestDep, client: AtlanClientDep, settings: AtlanSettingsDep) -> Response:
    """
    Deploy a data product or a single component starting from a provisioning descriptor
    """

    if isinstance(request, ValidationError):
        return check_response(out_response=request)

    data_product, component_id = request

    logger.info("Provisioning component with id: " + component_id)

    resp = provision_data_product(client, settings, data_product, component_id)

    return check_response(out_response=resp)


@app.get(
    "/v1/provision/{token}/status",
    response_model=None,
    responses={
        "200": {"model": ProvisioningStatus},
        "400": {"model": ValidationError},
        "500": {"model": SystemErr},
    },
    tags=["TechAdapter"],
)
def get_status(token: str) -> Response:
    """
    Get the status for a provisioning request.

    Not applicable: `/v1/provision` and `/v1/unprovision` complete synchronously,
    so there is never an asynchronous status to poll. Kept only to expose the
    standard Tech Adapter API surface.
    """

    resp = SystemErr(error="Response not yet implemented")

    return check_response(out_response=resp)


@app.post(
    "/v1/unprovision",
    response_model=None,
    responses={
        "200": {"model": ProvisioningStatus},
        "202": {"model": str},
        "400": {"model": ValidationError},
        "500": {"model": SystemErr},
    },
    tags=["TechAdapter"],
)
def unprovision(
    request: UnpackedUnprovisioningRequestDep, client: AtlanClientDep, settings: AtlanSettingsDep
) -> Response:
    """
    Undeploy a data product or a single component
    given the provisioning descriptor relative to the latest complete provisioning request
    """  # noqa: E501

    if isinstance(request, ValidationError):
        return check_response(out_response=request)

    data_product, component_id, remove_data = request

    logger.info("Unprovisioning component with id: " + component_id)

    resp = unprovision_data_product(client, settings, data_product, component_id, remove_data)

    return check_response(out_response=resp)


@app.post(
    "/v1/updateacl",
    response_model=None,
    responses={
        "200": {"model": ProvisioningStatus},
        "202": {"model": str},
        "400": {"model": ValidationError},
        "500": {"model": SystemErr},
    },
    tags=["TechAdapter"],
)
def updateacl(request: UnpackedUpdateAclRequestDep) -> Response:
    """
    Request the access to a tech adapter component

    Not applicable: this is a Data Catalog Plugin, not an access-control system for
    the underlying platform. It manages Atlan asset ownership (`owner_users`/
    `owner_groups`) from the descriptor as part of provision/unprovision, not as an
    independent ACL lifecycle; Atlan's native access controls govern read access.
    Reported as a successful no-op rather than an error, since no ACL
    reconciliation was ever expected to happen here.
    """  # noqa: E501

    if isinstance(request, ValidationError):
        return check_response(out_response=request)

    data_product, component_id, witboost_users = request

    logger.info("updateAcl requested for component '{}' — not applicable for this Data Catalog Plugin", component_id)

    resp = ProvisioningStatus(
        status=Status1.COMPLETED,
        result=(
            "updateAcl is not applicable for this Data Catalog Plugin: it does not "
            "manage access grants to the underlying data. Atlan's native access "
            "controls govern read access."
        ),
    )

    return check_response(out_response=resp)


@app.post(
    "/v1/validate",
    response_model=None,
    responses={"200": {"model": ValidationResult}, "500": {"model": SystemErr}},
    tags=["TechAdapter"],
)
def validate(request: UnpackedValidationRequestDep) -> Response:
    """
    Validate a provisioning request
    """

    if isinstance(request, ValidationError):
        return check_response(ValidationResult(valid=False, error=request))

    data_product, _component_id = request

    resp = validate_data_product(data_product)

    return check_response(out_response=resp)


@app.get(
    "/v1/entity/reference",
    response_model=None,
    responses={
        "200": {"model": EntityReference},
        "500": {"model": SystemErr},
    },
    tags=["DataCatalogPlugin"],
)
def get_entity_reference_endpoint(
    componentId: Annotated[str, Query(description="Output Port URN to get the reference to the Data Catalog entity")],
    client: AtlanClientDep,
    settings: AtlanSettingsDep,
) -> Response:
    """
    Return the reference (id) to the Data Catalog entity that refers
    to the provided Output Port. Synchronous. Since no descriptor is sent with this
    request, the matching Atlan asset is found by its `Output Port URN` Custom
    Metadata value rather than by a recomputed qualifiedName.
    """  # noqa: E501

    logger.info("Entity reference requested for component '{}'", componentId)

    resp = get_entity_reference(client, settings, componentId)

    return check_response(out_response=resp)


@app.post(
    "/v2/validate",
    response_model=None,
    responses={
        "202": {"model": str},
        "400": {"model": ValidationError},
        "500": {"model": SystemErr},
    },
    tags=["TechAdapter"],
)
def async_validate(
    body: ValidationRequest,
) -> Response:
    """
    Validate a deployment request asynchronously.

    Not applicable: this plugin only implements the synchronous `/v1/validate`.
    Kept only to expose the standard Tech Adapter API surface.
    """

    resp = SystemErr(error="Response not yet implemented")

    return check_response(out_response=resp)


@app.get(
    "/v2/validate/{token}/status",
    response_model=None,
    responses={
        "200": {"model": ValidationStatus},
        "400": {"model": ValidationError},
        "500": {"model": SystemErr},
    },
    tags=["TechAdapter"],
)
def get_validation_status(
    token: str,
) -> Response:
    """
    Get the status for an asynchronous validation request.

    Not applicable: `/v2/validate` is not implemented, so there is never an
    asynchronous validation status to poll. Kept only to expose the standard
    Tech Adapter API surface.
    """

    resp = SystemErr(error="Response not yet implemented")

    return check_response(out_response=resp)
