from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.errors import NotFoundError
from pyatlan.model.assets import Asset, Column, Connection, Database, Link, Schema, Table, View
from pyatlan.model.enums import EntityStatus
from pyatlan.model.fluent_search import FluentSearch
from pyatlan.model.response import AssetMutationResponse

from src.models.catalog_asset import CatalogAssetDescriptor, CatalogColumnDescriptor
from src.settings.atlan_settings import AtlanSettings

ASSET_TYPES: dict[str, type[Asset]] = {"Table": Table, "View": View}
"""Public: typeName -> pyatlan Asset subclass mapping for supported technical asset kinds. Reused by the provisioning orchestrator to resolve the pyatlan class needed for Phase 5/7 API calls (Custom Metadata, term association) against the same asset upserted here in Phase 4."""  # noqa: E501

# Column has separate parent-reference fields depending on whether it belongs to a
# Table or a View (docs/technical-details.md §Phase 4, column archival). Used to
# enumerate a parent asset's existing columns for archival.
_COLUMN_PARENT_QUALIFIED_NAME_FIELD = {
    Table: Column.TABLE_QUALIFIED_NAME,
    View: Column.VIEW_QUALIFIED_NAME,
}


class ConnectionNotFoundError(Exception):
    """Raised when the pre-provisioned Atlan Connection cannot be found (docs/technical-details.md §Phase 3, step 5)."""  # noqa: E501

    code = "CONNECTION_NOT_FOUND"


class AssetUpsertError(Exception):
    """Raised when the Database/Schema/Table/View/Column hierarchy cannot be upserted due to an Atlan API error (docs/technical-details.md §Phase 3/§Phase 4)."""  # noqa: E501

    code = "ASSET_UPSERT_FAILED"


@dataclass
class AssetUpsertResult:
    """
    Result of `upsert_technical_asset()`: the upserted asset's GUID plus the GUID of
    every one of its columns (by column name), needed by later phases that operate
    per-column (Phase 5's Custom Metadata and Phase 7's Business Term association) —
    see docs/technical-details.md §Phase 4, step 5 ("Collect the Atlan GUID of each
    created/updated asset").
    """  # noqa: E501

    asset_guid: str
    column_guids: dict[str, str] = field(default_factory=dict)


def ensure_parent_hierarchy(client: AtlanClient, catalog_asset: CatalogAssetDescriptor) -> None:
    """
    Phase 3: ensures the Database and Schema entities exist under the
    pre-provisioned Connection (docs/technical-details.md §Phase 3: Parent
    Hierarchy Upsert). The Connection itself is never created by the plugin — it
    must already exist in Atlan (populated via `connection_mapping` configuration).

    `Database.creator`/`Schema.creator` + `client.save()` are inherently
    idempotent/upsert-like in Atlan: entities are deduplicated by
    `typeName` + `qualifiedName`, so re-saving an already-existing Database/Schema
    with the same qualifiedName updates it in place rather than duplicating it.

    Raises:
        ConnectionNotFoundError: the configured Connection does not exist in Atlan.
        AssetUpsertError: any other Atlan API failure while upserting Database/Schema.
    """  # noqa: E501
    connection_qn = catalog_asset.connection_identity.qualified_name
    try:
        client.get_asset_by_qualified_name(connection_qn, Connection)
    except NotFoundError as exc:
        raise ConnectionNotFoundError(f"Atlan Connection '{connection_qn}' not found") from exc

    logger.info(
        "Catalog asset '{}' is under Connection '{}'", catalog_asset.asset_identity.qualified_name, connection_qn
    )

    database_qn = catalog_asset.database_identity.qualified_name
    catalog_name = database_qn.rsplit("/", 1)[-1]
    schema_qn = catalog_asset.schema_identity.qualified_name
    schema_name = schema_qn.rsplit("/", 1)[-1]

    logger.info(
        "Ensuring Database '{}' and Schema '{}' exist under Connection '{}'", database_qn, schema_qn, connection_qn
    )
    try:
        database = Database.creator(name=catalog_name, connection_qualified_name=connection_qn)
        client.save(database)

        schema = Schema.creator(name=schema_name, database_qualified_name=database_qn)
        client.save(schema)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(f"Failed to upsert parent hierarchy for '{schema_qn}': {exc}") from exc


def upsert_technical_asset(client: AtlanClient, catalog_asset: CatalogAssetDescriptor) -> AssetUpsertResult:
    """
    Phase 4: creates or updates the Table/View asset for the given
    `CatalogAssetDescriptor` and reconciles its columns (docs/technical-details.md
    §Phase 4: Technical Asset Upsert): new columns are added, changed columns
    (dataType/description) are updated, and columns present in Atlan but absent
    from the descriptor are archived (soft-deleted).

    Columns are matched deterministically by `<assetQualifiedName>/<columnName>`.

    Returns:
        AssetUpsertResult: the Atlan GUID of the upserted Table/View asset, plus the
        GUID of each of its columns (by column name).

    Raises:
        AssetUpsertError: the asset typeName is unsupported, or any Atlan API
            failure occurs while upserting/archiving the asset or its columns.
    """  # noqa: E501
    asset_type_name = catalog_asset.asset_identity.type_name
    asset_cls = ASSET_TYPES.get(asset_type_name)
    if asset_cls is None:
        raise AssetUpsertError(f"Unsupported asset typeName '{asset_type_name}'")

    asset_qn = catalog_asset.asset_identity.qualified_name
    schema_qn = catalog_asset.schema_identity.qualified_name

    existing_asset = _find_existing(client, asset_cls, asset_qn)
    if existing_asset is not None:
        _restore_if_archived(client, asset_cls, existing_asset)

    logger.info("Upserting {} '{}' ({})", asset_type_name, catalog_asset.name, "update" if existing_asset else "create")
    try:
        if existing_asset is not None:
            asset = asset_cls.updater(qualified_name=asset_qn, name=catalog_asset.name)
        else:
            asset = asset_cls.creator(name=catalog_asset.name, schema_qualified_name=schema_qn)
        asset.description = catalog_asset.description

        response = client.save(asset)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(f"Failed to upsert {asset_type_name} '{asset_qn}': {exc}") from exc

    asset_guid = _extract_guid(response, asset_cls, existing_asset)

    descriptor_column_names = {column.name for column in catalog_asset.columns}
    column_guids: dict[str, str] = {}
    for order, column in enumerate(catalog_asset.columns):
        column_guids[column.name] = _upsert_column(client, asset_cls, asset_qn, order, column)

    _archive_removed_columns(client, asset_cls, asset_qn, descriptor_column_names)

    return AssetUpsertResult(asset_guid=asset_guid, column_guids=column_guids)


def atlan_link(settings: AtlanSettings, guid: str, display_value: str) -> dict[str, Any]:
    """
    Public: builds the standard "View in Atlan" link structure used both in
    `provision`'s `publicInfo` (docs/technical-details.md §Phase 4/§Phase 6) and by
    the `entity/reference` endpoint (docs/HLD.md §Overview endpoint table), so both
    callers stay in sync on the same link shape.
    """  # noqa: E501
    return {
        "atlanLink": {
            "type": "string",
            "label": "View in Atlan",
            "value": display_value,
            "href": f"{settings.base_url}/assets/{guid}/overview",
        }
    }


_SOURCE_LINK_NAME = "Open in Databricks"


def ensure_source_system_link(
    client: AtlanClient,
    asset_cls: type[Asset],
    asset_qualified_name: str,
    asset_name: str,
    href: str,
) -> None:
    """
    Best-effort: attaches (idempotently) a native Atlan `Link` asset pointing to
    the technical asset's page in its source system (e.g. the Databricks table/view
    explorer URL from `OutputPort.source_table_url()`), so the "Links" tab of the
    Table/View shows an "Open in Databricks" shortcut alongside any other links.

    `idempotent=True` derives the Link's qualifiedName deterministically as
    `<asset_qualified_name>/<name>` (mirroring how Columns are keyed under their
    parent asset), so re-provisioning updates the same Link in place instead of
    creating a duplicate every run. The `asset` reference passed to `Link.creator`
    is built by constructing `asset_cls` directly with only `attributes.qualified_name`
    set — NOT via `asset_cls.updater()`, whose `@init_guid` decorator unconditionally
    overwrites `.guid` with a fake random negative placeholder (meant for bulk-create
    requests where multiple not-yet-existing entities reference each other by a
    shared temporary id). Since `Asset.trim_to_reference()` prefers `.guid` over
    `.qualified_name` when both could apply, a `updater()`-built reference would
    make `Link.creator` target that fake guid instead of the real, already-existing
    asset — which Atlan rejects with "Referenced entity <fake-guid> is not found".

    Never raises: any Atlan API failure is logged and swallowed, since this link is
    a convenience, not part of the documented Failure Matrix — provisioning must
    not fail just because the deep link could not be attached.
    """  # noqa: E501
    try:
        asset_ref = asset_cls(attributes=asset_cls.Attributes(qualified_name=asset_qualified_name, name=asset_name))
        link = Link.creator(asset=asset_ref, name=_SOURCE_LINK_NAME, link=href, idempotent=True)
        client.save(link)
    except Exception as exc:  # noqa: BLE001 - best-effort, never fails provisioning
        logger.warning("Failed to attach source system link to '{}': {}", asset_qualified_name, exc)


def find_existing_asset(client: AtlanClient, asset_cls: type[Asset], qualified_name: str) -> Asset | None:
    """Public: looks up an asset by typeName + qualifiedName, returning `None` (not raising) when it does not exist. Reused by the unprovisioning flow's idempotent "skip if missing" lookups (docs/technical-details.md §Unprovisioning, steps 2/4)."""  # noqa: E501
    try:
        return client.get_asset_by_qualified_name(qualified_name, asset_cls)
    except NotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(f"Failed to look up existing asset '{qualified_name}': {exc}") from exc


def find_columns(client: AtlanClient, asset_cls: type[Asset], asset_qualified_name: str) -> list[Column]:
    """Public: enumerates all Columns currently attached to the given Table/View in Atlan. Reused by column archival (Phase 4) and by the unprovisioning flow, which must remove an asset's columns alongside the asset itself (docs/technical-details.md §Unprovisioning, step 3b)."""  # noqa: E501
    parent_field = _COLUMN_PARENT_QUALIFIED_NAME_FIELD[asset_cls]
    try:
        return list(FluentSearch(wheres=[parent_field.eq(asset_qualified_name)]).execute(client=client))
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(f"Failed to enumerate existing columns for '{asset_qualified_name}': {exc}") from exc


def remove_technical_asset(
    client: AtlanClient,
    asset_cls: type[Asset],
    qualified_name: str,
    strategy: str,
) -> str | None:
    """
    Removes the Table/View identified by `qualified_name` (and all of its columns)
    from Atlan, per the configured `unprovision.strategy` (docs/technical-details.md
    §Unprovisioning, step 3b; §unprovision): `"archive"` soft-deletes (reversible),
    any other value (`"purge"`) permanently deletes.

    Idempotent: if the asset does not exist, this is a no-op (docs/technical-details.md
    §Unprovisioning, step 4 — "If not found → skip").

    Returns:
        str | None: the removed asset's GUID, or `None` if it did not exist.

    Raises:
        AssetUpsertError: any Atlan API failure while looking up or removing the
            asset/columns.
    """  # noqa: E501
    asset = find_existing_asset(client, asset_cls, qualified_name)
    if asset is None:
        return None

    columns = find_columns(client, asset_cls, qualified_name)
    guids_to_remove = [column.guid for column in columns] + [asset.guid]

    logger.info(
        "{} {} '{}' and its {} column(s)",
        "Purging" if strategy == "purge" else "Archiving",
        asset_cls.__name__,
        qualified_name,
        len(columns),
    )
    try:
        if strategy == "purge":
            client.asset.purge_by_guid(guids_to_remove)
        else:
            client.asset.delete_by_guid(guids_to_remove)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(f"Failed to remove asset '{qualified_name}': {exc}") from exc

    return asset.guid


def _find_existing(client: AtlanClient, asset_cls: type[Asset], qualified_name: str) -> Asset | None:
    return find_existing_asset(client, asset_cls, qualified_name)


def _restore_if_archived(client: AtlanClient, asset_cls: type[Asset], existing: Asset) -> None:
    """
    Restores an archived (soft-deleted) asset back to ACTIVE before it is updated.

    Without this, re-provisioning a Table/View/Column after an "archive"-strategy
    unprovision (docs/technical-details.md §Unprovisioning) still finds and updates
    the existing asset by qualifiedName, but leaves it visibly "(archived)" in the
    Atlan UI since a plain attribute update never changes entity status.
    """  # noqa: E501
    if existing.status != EntityStatus.DELETED or existing.qualified_name is None:
        return
    logger.info(
        "Restoring archived {} '{}' so re-provisioning stays idempotent", asset_cls.__name__, existing.qualified_name
    )  # noqa: E501
    try:
        client.asset.restore(asset_cls, existing.qualified_name)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(
            f"Failed to restore archived {asset_cls.__name__} '{existing.qualified_name}': {exc}"
        ) from exc  # noqa: E501


def _extract_guid(response: AssetMutationResponse, asset_cls: type[Asset], existing_asset: Asset | None) -> str:
    created = response.assets_created(asset_cls)
    if created:
        return created[0].guid
    updated = response.assets_updated(asset_cls)
    if updated:
        return updated[0].guid
    if existing_asset is not None:
        # Atlan performed a no-op save (nothing actually changed); reuse the GUID
        # already resolved by the pre-save lookup.
        return existing_asset.guid
    raise AssetUpsertError("Atlan did not return the created/updated asset")


def _upsert_column(
    client: AtlanClient,
    asset_cls: type[Asset],
    asset_qualified_name: str,
    order: int,
    column: CatalogColumnDescriptor,
) -> str:
    column_qn = f"{asset_qualified_name}/{column.name}"
    existing_column = _find_existing(client, Column, column_qn)
    if existing_column is not None:
        _restore_if_archived(client, Column, existing_column)

    try:
        if existing_column is not None:
            col = Column.updater(qualified_name=column_qn, name=column.name)
        else:
            col = Column.creator(
                name=column.name,
                parent_qualified_name=asset_qualified_name,
                parent_type=asset_cls,
                order=order,
            )
        col.data_type = column.data_type
        col.description = column.description
        response = client.save(col)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(f"Failed to upsert Column '{column_qn}': {exc}") from exc

    return _extract_guid(response, Column, existing_column)


def _archive_removed_columns(
    client: AtlanClient,
    asset_cls: type[Asset],
    asset_qualified_name: str,
    descriptor_column_names: set[str],
) -> None:
    """
    Archives (soft-deletes) columns that exist on the asset in Atlan but are no
    longer present in the descriptor (docs/technical-details.md §Phase 4, step 4).
    """  # noqa: E501
    results = find_columns(client, asset_cls, asset_qualified_name)

    columns_to_archive = [asset for asset in results if asset.name not in descriptor_column_names]
    if not columns_to_archive:
        return

    logger.info(
        "Archiving {} column(s) removed from the descriptor for '{}'",
        len(columns_to_archive),
        asset_qualified_name,
    )
    try:
        client.asset.delete_by_guid([column.guid for column in columns_to_archive])
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise AssetUpsertError(f"Failed to archive removed columns for '{asset_qualified_name}': {exc}") from exc
