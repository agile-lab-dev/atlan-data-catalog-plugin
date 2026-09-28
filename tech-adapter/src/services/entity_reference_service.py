from __future__ import annotations

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.model.assets import Asset
from pyatlan.model.fields.atlan_fields import CustomMetadataField
from pyatlan.model.fluent_search import FluentSearch

from src.models.api_models import EntityReference, SystemErr
from src.services.asset_service import ASSET_TYPES, atlan_link
from src.settings.atlan_settings import AtlanSettings

_OUTPUT_PORT_URN_ATTRIBUTE = "Output Port URN"


class EntityNotFoundError(Exception):
    """Raised when no Atlan asset carries the given `componentId` as its `Output Port URN` Custom Metadata value."""  # noqa: E501

    code = "ENTITY_NOT_FOUND"


class EntityReferenceLookupError(Exception):
    """Raised when the Atlan search for the referenced entity fails for reasons other than a missing match."""  # noqa: E501

    code = "ENTITY_REFERENCE_LOOKUP_FAILED"


def get_entity_reference(
    client: AtlanClient,
    settings: AtlanSettings,
    component_id: str,
) -> EntityReference | SystemErr:
    """
    Implements `GET /v1/entity/reference?componentId=...` (docs/HLD.md §Overview
    endpoint table).

    No descriptor is available on this request — only the Output Port's
    `componentId` — so the Atlan `qualifiedName` cannot be recomputed the way
    `provision`/`unprovision` do. Instead, this looks up the Table/View asset whose
    `Output Port URN` Custom Metadata attribute (written during `provision`, see
    `provision_service.py` Phase 5) equals `componentId`.

    If more than one asset matches (not expected in normal operation — Output Port
    URNs are unique per component), the first match is used and a warning is
    logged rather than treating it as an error.

    Returns:
        EntityReference: the Atlan link for the matching asset.
        SystemErr: no asset matches (`ENTITY_NOT_FOUND`), or the Atlan search
            itself failed (`ENTITY_REFERENCE_LOOKUP_FAILED`).
    """  # noqa: E501
    logger.info("Looking up Data Catalog entity reference for component '{}'", component_id)

    try:
        matches = list(
            FluentSearch()
            .where(Asset.TYPE_NAME.within(list(ASSET_TYPES.keys())))
            .where(_output_port_urn_field(client, settings).eq(component_id))
            .execute(client=client)
        )
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        lookup_error = EntityReferenceLookupError(f"Failed to search for componentId '{component_id}': {exc}")
        logger.error("{}: {}", lookup_error.code, lookup_error)
        return SystemErr(error=f"{lookup_error.code}: {lookup_error}")

    if not matches:
        not_found_error = EntityNotFoundError(f"No Atlan asset found for componentId '{component_id}'")
        logger.warning("{}: {}", not_found_error.code, not_found_error)
        return SystemErr(error=f"{not_found_error.code}: {not_found_error}")

    if len(matches) > 1:
        logger.warning(
            "Found {} Atlan assets for componentId '{}' — expected at most one; using the first match",
            len(matches),
            component_id,
        )

    asset = matches[0]
    link = atlan_link(settings, asset.guid, asset.name)
    return EntityReference(reference=link["atlanLink"]["href"])


def _output_port_urn_field(client: AtlanClient, settings: AtlanSettings) -> CustomMetadataField:
    type_name = settings.custom_metadata.get("type-name", "Witboost")
    return CustomMetadataField(client, type_name, _OUTPUT_PORT_URN_ATTRIBUTE)
