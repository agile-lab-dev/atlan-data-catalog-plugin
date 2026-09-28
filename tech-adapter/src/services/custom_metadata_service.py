from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.model.assets import Asset
from pyatlan.model.custom_metadata import CustomMetadataDict

from src.settings.atlan_settings import AtlanSettings

CustomMetadataValue = str | datetime | None


class CustomMetadataApplyError(Exception):
    """Raised when Custom Metadata values cannot be written to (or verified on) an asset (docs/technical-details.md §Phase 5)."""  # noqa: E501

    code = "CUSTOM_METADATA_FAILED"


def apply_custom_metadata(
    client: AtlanClient,
    settings: AtlanSettings,
    guid: str,
    asset_type: type[Asset],
    values: Mapping[str, CustomMetadataValue],
) -> None:
    """
    Phase 5: sets the given Witboost Custom Metadata attribute values (by display
    name) on the asset identified by `guid` (docs/technical-details.md §Phase 5:
    Apply Custom Metadata).

    Uses pyatlan's `CustomMetadataDict`, which resolves and caches the
    {display_name: 22-character attribute id} mapping internally via
    `CustomMetadataCache` — this satisfies the documented "never address attributes
    by display name" constraint by construction, without any manual id caching in
    this plugin.

    Values for attributes whose underlying Atlan type is `date` are coerced to
    epoch-milliseconds before being written: Atlan's API rejects ISO 8601 strings
    for `date`-typed custom metadata attributes (ATLAS-404-00-007 "invalid value
    for type date"), so `datetime` objects and ISO strings passed here are
    converted automatically based on the attribute's actual type, resolved via
    `CustomMetadataCache`.

    After writing, reads back the asset's custom metadata and verifies at least one
    of the written values round-trips correctly (docs/technical-details.md §Phase 5,
    step 2), to catch a silently-misdirected write early.

    Args:
        values: display_name -> value. `None` values clear the attribute.
            `datetime`/ISO-string values are only valid for attributes whose
            Atlan type is `date`.

    Raises:
        CustomMetadataApplyError: any Atlan API failure while writing or verifying,
            or the read-back verification does not match what was written.
    """  # noqa: E501
    type_name = settings.custom_metadata.get("type-name", "Witboost")

    try:
        custom_metadata = CustomMetadataDict(client=client, name=type_name)
        for display_name, value in values.items():
            custom_metadata[display_name] = _coerce_value(client, type_name, display_name, value)
        client.update_custom_metadata_attributes(guid, custom_metadata)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise CustomMetadataApplyError(f"Failed to apply Custom Metadata to asset '{guid}': {exc}") from exc

    _verify_write(client, settings, guid, asset_type, values, type_name)


def _coerce_value(
    client: AtlanClient, type_name: str, display_name: str, value: CustomMetadataValue
) -> str | datetime | int | None:
    """
    Converts `value` to the representation Atlan expects for the attribute's
    underlying type. Only `date`-typed attributes need coercion (to epoch
    milliseconds); every other type is passed through unchanged.
    """
    if value is None:
        return None

    cache = client.custom_metadata_cache
    attr_id = cache.get_attr_id_for_name(set_name=type_name, attr_name=display_name)
    attr_def = cache.get_attribute_def(attr_id=attr_id)

    if attr_def.type_name != "date":
        return value

    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return int(dt.timestamp() * 1000)


def _verify_write(
    client: AtlanClient,
    settings: AtlanSettings,
    guid: str,
    asset_type: type[Asset],
    values: Mapping[str, CustomMetadataValue],
    type_name: str,
) -> None:
    display_name, expected_value = next(iter(values.items()))
    coerced_expected_value = _coerce_value(client, type_name, display_name, expected_value)

    try:
        refreshed_asset = client.get_asset_by_guid(guid, asset_type)
        actual_custom_metadata = refreshed_asset.get_custom_metadata(client=client, name=type_name)
        actual_value = actual_custom_metadata[display_name]
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise CustomMetadataApplyError(f"Failed to verify Custom Metadata write on asset '{guid}': {exc}") from exc

    if actual_value != coerced_expected_value:
        raise CustomMetadataApplyError(
            f"Custom Metadata write verification failed for asset '{guid}': "
            f"attribute '{display_name}' expected '{coerced_expected_value}', got '{actual_value}'"
        )

    logger.debug("Verified Custom Metadata write on asset '{}' (attribute '{}')", guid, display_name)
