from __future__ import annotations

from typing import Any

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.model.enums import EntityStatus

from src.models.api_models import Info, ProvisioningStatus, Status1, SystemErr
from src.models.data_product_descriptor import DataProduct
from src.resolvers.registry import OutputPortResolverRegistry
from src.services.asset_service import ASSET_TYPES, remove_technical_asset
from src.services.data_product_service import (
    DataProductUpsertError,
    compute_display_name,
    compute_slug,
    ensure_domain_exists,
    find_data_product_by_name_or_qualified_name,
)
from src.settings.atlan_settings import AtlanSettings


def unprovision_data_product(
    client: AtlanClient,
    settings: AtlanSettings,
    data_product: DataProduct,
    component_id: str,
    remove_data: bool,
) -> ProvisioningStatus | SystemErr:
    """
    Runs the unprovisioning flow documented in docs/technical-details.md
    §Unprovisioning against the given Data Product descriptor, synchronously.

    Mirrors `provision_data_product`'s "process every Output Port present in the
    descriptor" contract (see its docstring) rather than filtering by
    `component_id` — this is a Data Catalog Plugin operating on the descriptor as a
    whole, not a per-component Tech Adapter.

    `remove_data` (from the unprovisioning request's `removeData` field) selects a
    hard delete (`purge`) for this call, overriding the configured
    `unprovision.strategy` default — the descriptor-level "also delete underlying
    data" signal takes precedence over the adapter-wide default, matching the field's
    documented semantics ("its underlying data will also be deleted").

    Connection, DataDomain, and Business Terms are never deleted
    (docs/technical-details.md §Unprovisioning, step 7).

    Returns:
        ProvisioningStatus(status=COMPLETED, ...): every phase succeeded, including
            the idempotent no-op case where an asset was already missing.
        SystemErr: any phase failed; `error` carries `"<CODE>: <message>"`.
    """  # noqa: E501
    logger.info("Unprovisioning Data Product '{}' (triggered by component '{}')", data_product.id, component_id)

    strategy = "purge" if remove_data else settings.unprovision.get("strategy", "archive")

    try:
        registry = OutputPortResolverRegistry()
        public_info: dict[str, Any] = {}

        for output_port in data_product.get_output_ports():
            catalog_asset = registry.resolve(output_port, settings, data_product.environment)
            if catalog_asset is None:
                continue  # unsupported technology — nothing was ever cataloged for it

            asset_type = ASSET_TYPES[catalog_asset.asset_identity.type_name]
            removed_guid = remove_technical_asset(
                client, asset_type, catalog_asset.asset_identity.qualified_name, strategy
            )
            if removed_guid is None:
                logger.info("Output Port '{}' has no matching asset in Atlan — skipping (idempotent)", output_port.id)  # noqa: E501
                continue

            public_info[output_port.id] = {"removed": True}

        _remove_data_product(client, settings, data_product, strategy)

        return ProvisioningStatus(
            status=Status1.COMPLETED,
            result=f"Data Product '{data_product.name}' unprovisioned successfully from Atlan",
            info=Info(publicInfo=public_info, privateInfo={}),
        )

    except Exception as exc:  # noqa: BLE001 - translate any phase failure into the documented SystemErr code
        code = getattr(exc, "code", "UNEXPECTED_ERROR")
        logger.error("Unprovisioning failed for Data Product '{}': {}: {}", data_product.id, code, exc)
        return SystemErr(error=f"{code}: {exc}")


def _remove_data_product(
    client: AtlanClient,
    settings: AtlanSettings,
    data_product: DataProduct,
    strategy: str,
) -> None:
    """
    Docs/technical-details.md §Unprovisioning, steps 5-6: since this plugin always
    (re-)processes every Output Port present in the descriptor on every call, same
    as `provision_data_product` (docs/HLD.md Phase 0 "extract ... ALL Output
    Ports" — this is a Data Catalog Plugin operating on the whole descriptor, not
    a per-component Tech Adapter), unprovisioning a Data Product always means
    tearing down the *entire* DataProduct, not just a subset of its Output Ports.
    The DataProduct itself is therefore always archived/purged (using the same
    `strategy` as its Output Ports) whenever it exists, rather than conditionally
    kept alive based on a "remaining linked GUIDs" calculation that would only be
    meaningful for a per-component Tech Adapter.

    A no-op if the DataProduct was never created (e.g. every Output Port in this
    descriptor had an unsupported technology and nothing was ever cataloged).
    """  # noqa: E501
    # `ensure_domain_exists` (not the raw `settings.domain_qualified_name`) resolves
    # `domain_mapping` values that are configured as the DataDomain's human-readable
    # UI name rather than its real qualifiedName — required here because
    # `domain_qn` is also used below as a qualifiedName-prefix filter for the
    # by-name DataProduct fallback search, which would never match anything if
    # `domain_qn` were left as an unresolved display name.
    domain_qn = ensure_domain_exists(client, settings, data_product.domain)
    dp_qn = f"{domain_qn}/product/{compute_slug(data_product)}"

    # Uses the same by-name (including archived) fallback search as
    # `upsert_data_product()` — the qualifiedName recomputed above may not match
    # the live entity's actual qualifiedName (e.g. it was found/restored via that
    # same fallback during a prior provisioning call), so a plain qualifiedName
    # lookup here could silently no-op and leave the DataProduct un-archived.
    data_product_asset = find_data_product_by_name_or_qualified_name(
        client, dp_qn, compute_display_name(data_product), domain_qn
    )
    if data_product_asset is None:
        logger.info("DataProduct '{}' not found — nothing to remove (idempotent)", dp_qn)
        return
    if data_product_asset.status == EntityStatus.DELETED:
        logger.info("DataProduct '{}' is already archived — nothing to do (idempotent)", dp_qn)
        return

    logger.info("{} DataProduct '{}'", "Purging" if strategy == "purge" else "Archiving", dp_qn)
    try:
        if strategy == "purge":
            client.asset.purge_by_guid([data_product_asset.guid])
        else:
            client.asset.delete_by_guid([data_product_asset.guid])
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise DataProductUpsertError(f"Failed to remove DataProduct '{dp_qn}': {exc}") from exc
