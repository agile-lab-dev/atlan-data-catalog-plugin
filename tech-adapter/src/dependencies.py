from typing import Annotated, Tuple

import yaml
from fastapi import Depends

from src.models.api_models import (
    DescriptorKind,
    ProvisioningRequest,
    UpdateAclRequest,
    ValidationError,
)
from src.models.data_product_descriptor import DataProduct
from src.utility.parsing_pydantic_models import parse_yaml_with_model


async def _unpack_data_product_descriptor(
    provisioning_request: ProvisioningRequest, expected_kind: DescriptorKind
) -> Tuple[DataProduct, str] | ValidationError:
    """
    Parses `provisioning_request.descriptor` as a complete Data Product descriptor.

    Args:
        provisioning_request (ProvisioningRequest): The request to be unpacked.
        expected_kind (DescriptorKind): The `descriptorKind` this operation requires.

    Returns:
        Union[Tuple[DataProduct, str], ValidationError]:
            - If successful, returns a tuple containing:
                - `DataProduct`: The parsed Data Product.
                - `str`: The Data Product's own `id`, used only for
                  logging/traceability.
            - If unsuccessful, returns a `ValidationError` object with error details.
    """  # noqa: E501

    if not provisioning_request.descriptorKind == expected_kind:
        error = (
            f"Expecting a {expected_kind} but got a "
            f"{provisioning_request.descriptorKind} instead; please check with the "
            f"platform team."
        )
        return ValidationError(errors=[error])
    try:
        descriptor_dict = yaml.safe_load(provisioning_request.descriptor)
        data_product = parse_yaml_with_model(descriptor_dict, DataProduct)

        if isinstance(data_product, DataProduct):
            return data_product, data_product.id
        elif isinstance(data_product, ValidationError):
            return data_product

        else:
            return ValidationError(
                errors=[
                    "An unexpected error occurred while parsing the provisioning request."  # noqa: E501
                ]
            )

    except Exception as ex:
        return ValidationError(errors=["Unable to parse the descriptor.", str(ex)])


async def unpack_validation_request(
    provisioning_request: ProvisioningRequest,
) -> Tuple[DataProduct, str] | ValidationError:
    """
    Unpacks a Validation Request.

    `/v1/validate` runs before any provisioning has happened, so it expects
    `DescriptorKind.DATAPRODUCT_DESCRIPTOR`: the plain Data Product descriptor, not
    yet enriched with provisioning results (see `unpack_provisioning_request` for the
    enriched variant expected by `/v1/provision` and `/v1/unprovision`).

    Args:
        provisioning_request (ProvisioningRequest): The validation request to be unpacked.

    Returns:
        Union[Tuple[DataProduct, str], ValidationError]:
            - If successful, returns a tuple containing:
                - `DataProduct`: The data product to validate.
                - `str`: The Data Product's own `id`, used only for
                  logging/traceability.
            - If unsuccessful, returns a `ValidationError` object with error details.
    """  # noqa: E501

    return await _unpack_data_product_descriptor(provisioning_request, DescriptorKind.DATAPRODUCT_DESCRIPTOR)


UnpackedValidationRequestDep = Annotated[
    Tuple[DataProduct, str] | ValidationError,
    Depends(unpack_validation_request),
]


async def unpack_provisioning_request(
    provisioning_request: ProvisioningRequest,
) -> Tuple[DataProduct, str] | ValidationError:
    """
    Unpacks a Provisioning Request.

    `/v1/provision` and `/v1/unprovision` receive the Data Product descriptor after
    every other Tech Adapter has provisioned, enriched with their results
    at both the Data Product and component level. This plugin does not need that
    enrichment for cataloging, but must accept it: it expects
    `DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS` and relies on
    the descriptor models' `extra="allow"` configuration (see
    `models/data_product_descriptor.py`) to ignore the enrichment fields it does not
    use.

    Args:
        provisioning_request (ProvisioningRequest): The provisioning request to be unpacked.

    Returns:
        Union[Tuple[DataProduct, str], ValidationError]:
            - If successful, returns a tuple containing:
                - `DataProduct`: The data product for provisioning.
                - `str`: The Data Product's own `id`, used only for
                  logging/traceability.
            - If unsuccessful, returns a `ValidationError` object with error details.
    """  # noqa: E501

    return await _unpack_data_product_descriptor(
        provisioning_request, DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS
    )


UnpackedProvisioningRequestDep = Annotated[
    Tuple[DataProduct, str] | ValidationError,
    Depends(unpack_provisioning_request),
]


async def unpack_unprovisioning_request(
    provisioning_request: ProvisioningRequest,
) -> Tuple[DataProduct, str, bool] | ValidationError:
    """
    Unpacks a Unprovisioning Request.

    This function takes a `ProvisioningRequest` object and extracts relevant information
    to perform unprovisioning for a data product component.

    Args:
        provisioning_request (ProvisioningRequest): The unprovisioning request to be unpacked.

    Returns:
        Union[Tuple[DataProduct, str, bool], ValidationError]:
            - If successful, returns a tuple containing:
                - `DataProduct`: The data product for provisioning.
                - `str`: The component ID to provision.
                - `bool`: The value of the removeData field.
            - If unsuccessful, returns a `ValidationError` object with error details.

    Note:
        - This function expects the `provisioning_request` to have a descriptor kind of `DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS` (see `unpack_provisioning_request`).
        - It will attempt to parse the descriptor and return the relevant information. If parsing fails or the descriptor kind is unexpected, a `ValidationError` will be returned.

    """  # noqa: E501

    unpacked_request = await unpack_provisioning_request(provisioning_request)
    remove_data = provisioning_request.removeData if provisioning_request.removeData is not None else False

    if isinstance(unpacked_request, ValidationError):
        return unpacked_request
    else:
        data_product, component_id = unpacked_request
        return data_product, component_id, remove_data


UnpackedUnprovisioningRequestDep = Annotated[
    Tuple[DataProduct, str, bool] | ValidationError,
    Depends(unpack_unprovisioning_request),
]


async def unpack_update_acl_request(
    update_acl_request: UpdateAclRequest,
) -> Tuple[DataProduct, str, list[str]] | ValidationError:
    """
    Unpacks an Update ACL Request.

    This function takes an `UpdateAclRequest` object and extracts relevant information
    to update access control lists (ACL) for a data product.

    Args:
        update_acl_request (UpdateAclRequest): The update ACL request to be unpacked.

    Returns:
        Union[Tuple[DataProduct, str, List[str]], ValidationError]:
            - If successful, returns a tuple containing:
                - `DataProduct`: The data product to update ACL for.
                - `str`: The component ID to provision.
                - `List[str]`: A list of references.
            - If unsuccessful, returns a `ValidationError` object with error details.

    Note:
        This function expects the `update_acl_request.provisionInfo.request` field
        to contain the complete Data Product descriptor (same
        `DATAPRODUCT_DESCRIPTOR` shape as `unpack_provisioning_request`, not a
        `dataProduct`/`componentIdToProvision` wrapper), since this is a Data
        Catalog Plugin operating on the whole descriptor rather than a single
        component. The returned `str` is the Data Product's own `id`, used only for
        logging/traceability. If parsing fails, a `ValidationError` will be
        returned.

    """  # noqa: E501

    try:
        request = yaml.safe_load(update_acl_request.provisionInfo.request)
        data_product = parse_yaml_with_model(request, DataProduct)
        if isinstance(data_product, DataProduct):
            return (
                data_product,
                data_product.id,
                update_acl_request.refs,
            )
        elif isinstance(data_product, ValidationError):
            return data_product
        else:
            return ValidationError(errors=["An unexpected error occurred while parsing the update acl request."])
    except Exception as ex:
        return ValidationError(errors=["Unable to parse the descriptor.", str(ex)])


UnpackedUpdateAclRequestDep = Annotated[
    Tuple[DataProduct, str, list[str]] | ValidationError,
    Depends(unpack_update_acl_request),
]
