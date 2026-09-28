from __future__ import annotations

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.errors import NotFoundError
from pyatlan.model.enums import AtlanCustomAttributePrimitiveType
from pyatlan.model.typedef import AttributeDef, CustomMetadataDef

from src.models.constants import CUSTOM_METADATA_ATTRIBUTES
from src.settings.atlan_settings import AtlanSettings


class CustomMetadataDefinitionError(Exception):
    """Raised when the Witboost Custom Metadata typedef cannot be ensured (docs/technical-details.md §Phase 2, step 1)."""  # noqa: E501

    code = "CUSTOM_METADATA_DEF_FAILED"


def ensure_custom_metadata_def(client: AtlanClient, settings: AtlanSettings) -> None:
    """
    Ensures the Witboost Custom Metadata type exists in Atlan with its 9 attributes
    (docs/technical-details.md §Phase 2: Prerequisite Metadata Definitions,
    §Configuration → custom-metadata).

    Idempotent: a no-op if the typedef already exists. Attribute *id* resolution
    (Atlan addresses attributes by a generated 22-character id, never by display
    name) is intentionally NOT done here — it is delegated to pyatlan's own
    `CustomMetadataCache`/`CustomMetadataDict` abstractions at write time (Phase 5),
    which resolve and cache the {display_name: id} mapping internally. This function
    only guarantees the typedef itself, and its attributes, exist.

    Raises:
        CustomMetadataDefinitionError: the typedef is missing and either
            `custom-metadata.auto-create` is disabled, or typedef creation fails.
    """  # noqa: E501
    type_name = settings.custom_metadata.get("type-name", "Witboost")
    auto_create = settings.custom_metadata.get("auto-create", True)

    try:
        client.custom_metadata_cache.get_custom_metadata_def(type_name)
        logger.info("Custom Metadata type '{}' already exists in Atlan", type_name)
        return
    except NotFoundError:
        pass

    if not auto_create:
        raise CustomMetadataDefinitionError(
            f"Custom Metadata type '{type_name}' does not exist in Atlan and "
            "'custom-metadata.auto-create' is disabled"
        )

    logger.info(
        "Creating Custom Metadata type '{}' with {} attributes",
        type_name,
        len(CUSTOM_METADATA_ATTRIBUTES),
    )
    try:
        typedef = CustomMetadataDef.create(display_name=type_name)
        typedef.attribute_defs = [
            AttributeDef.create(
                client=client,
                display_name=attribute_name,
                attribute_type=AtlanCustomAttributePrimitiveType.STRING,
            )
            for attribute_name in CUSTOM_METADATA_ATTRIBUTES
        ]
        client.create_typedef(typedef)
        client.custom_metadata_cache.refresh_cache()
    except CustomMetadataDefinitionError:
        raise
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise CustomMetadataDefinitionError(f"Failed to create Custom Metadata type '{type_name}': {exc}") from exc
