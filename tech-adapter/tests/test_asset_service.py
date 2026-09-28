from unittest import mock

import pytest
from pyatlan.errors import ErrorCode
from pyatlan.model.assets import Column, Connection, Database, Link, Schema, View
from pyatlan.model.enums import EntityStatus

from src.models.catalog_asset import AssetIdentity, CatalogAssetDescriptor, CatalogColumnDescriptor
from src.services.asset_service import (
    AssetUpsertError,
    ConnectionNotFoundError,
    ensure_parent_hierarchy,
    ensure_source_system_link,
    remove_technical_asset,
    upsert_technical_asset,
)


def _not_found_error():
    return ErrorCode.API_TOKEN_NOT_FOUND_BY_NAME.exception_with_parameters("x")


def _catalog_asset(**overrides) -> CatalogAssetDescriptor:
    defaults = dict(
        connection_identity=AssetIdentity(type_name="Connection", qualified_name="default/databricks/123"),
        database_identity=AssetIdentity(type_name="Database", qualified_name="default/databricks/123/sales_catalog"),
        schema_identity=AssetIdentity(
            type_name="Schema", qualified_name="default/databricks/123/sales_catalog/sales_schema"
        ),
        asset_identity=AssetIdentity(
            type_name="View",
            qualified_name="default/databricks/123/sales_catalog/sales_schema/orders_view",
        ),
        name="orders_view",
        description="Orders view",
        columns=[CatalogColumnDescriptor(name="order_id", data_type="string", description="the id")],
    )
    defaults.update(overrides)
    return CatalogAssetDescriptor(**defaults)


# --- Phase 3: ensure_parent_hierarchy ---


def test_ensure_parent_hierarchy_raises_when_connection_missing():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found_error()

    with pytest.raises(ConnectionNotFoundError):
        ensure_parent_hierarchy(client, _catalog_asset())

    client.save.assert_not_called()


def test_ensure_parent_hierarchy_saves_database_and_schema_when_connection_exists():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.return_value = mock.MagicMock(spec=Connection)

    ensure_parent_hierarchy(client, _catalog_asset())

    assert client.save.call_count == 2
    saved_database = client.save.call_args_list[0][0][0]
    saved_schema = client.save.call_args_list[1][0][0]
    assert isinstance(saved_database, Database)
    assert isinstance(saved_schema, Schema)


def test_ensure_parent_hierarchy_wraps_atlan_api_errors_on_save():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.return_value = mock.MagicMock(spec=Connection)
    client.save.side_effect = RuntimeError("boom")

    with pytest.raises(AssetUpsertError):
        ensure_parent_hierarchy(client, _catalog_asset())


# --- Phase 4: upsert_technical_asset ---


def test_upsert_technical_asset_creates_when_missing():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found_error()
    created_view = mock.MagicMock(spec=View, guid="guid-1")
    save_view_response = mock.MagicMock()
    save_view_response.assets_created.return_value = [created_view]
    save_view_response.assets_updated.return_value = []
    created_column = mock.MagicMock(spec=Column, guid="guid-col")
    save_column_response = mock.MagicMock()
    save_column_response.assets_created.return_value = [created_column]
    save_column_response.assets_updated.return_value = []
    client.save.side_effect = [save_view_response, save_column_response]
    client.asset.search.return_value = []

    guid = upsert_technical_asset(client, _catalog_asset()).asset_guid

    assert guid == "guid-1"
    assert client.save.call_count == 2


def test_upsert_technical_asset_updates_when_existing():
    client = mock.MagicMock()
    existing_view = mock.MagicMock(spec=View, guid="existing-guid", status=EntityStatus.ACTIVE)
    existing_column = mock.MagicMock(spec=Column, guid="existing-col-guid", status=EntityStatus.ACTIVE)

    def get_asset_side_effect(qualified_name, asset_type):
        if asset_type is View:
            return existing_view
        if asset_type is Column:
            return existing_column
        raise AssertionError("unexpected asset type")

    client.get_asset_by_qualified_name.side_effect = get_asset_side_effect

    no_op_response = mock.MagicMock()
    no_op_response.assets_created.return_value = []
    no_op_response.assets_updated.return_value = []
    client.save.return_value = no_op_response
    client.asset.search.return_value = []

    guid = upsert_technical_asset(client, _catalog_asset()).asset_guid

    assert guid == "existing-guid"
    client.asset.restore.assert_not_called()


def test_upsert_technical_asset_restores_archived_asset_and_columns():
    client = mock.MagicMock()
    asset_qn = "default/databricks/123/sales_catalog/sales_schema/orders_view"
    column_qn = f"{asset_qn}/order_id"
    archived_view = mock.MagicMock(
        spec=View, guid="existing-guid", qualified_name=asset_qn, status=EntityStatus.DELETED
    )  # noqa: E501
    archived_column = mock.MagicMock(
        spec=Column, guid="existing-col-guid", qualified_name=column_qn, status=EntityStatus.DELETED
    )  # noqa: E501

    def get_asset_side_effect(qualified_name, asset_type):
        if asset_type is View:
            return archived_view
        if asset_type is Column:
            return archived_column
        raise AssertionError("unexpected asset type")

    client.get_asset_by_qualified_name.side_effect = get_asset_side_effect

    no_op_response = mock.MagicMock()
    no_op_response.assets_created.return_value = []
    no_op_response.assets_updated.return_value = []
    client.save.return_value = no_op_response
    client.asset.search.return_value = []

    upsert_technical_asset(client, _catalog_asset())

    assert client.asset.restore.call_count == 2
    client.asset.restore.assert_any_call(View, asset_qn)
    client.asset.restore.assert_any_call(Column, column_qn)


def test_upsert_technical_asset_wraps_atlan_api_error_on_restore():
    client = mock.MagicMock()
    asset_qn = "default/databricks/123/sales_catalog/sales_schema/orders_view"
    archived_view = mock.MagicMock(
        spec=View, guid="existing-guid", qualified_name=asset_qn, status=EntityStatus.DELETED
    )  # noqa: E501
    client.get_asset_by_qualified_name.return_value = archived_view
    client.asset.restore.side_effect = RuntimeError("boom")

    with pytest.raises(AssetUpsertError):
        upsert_technical_asset(client, _catalog_asset())

    client.save.assert_not_called()


def test_upsert_technical_asset_returns_column_guids():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found_error()
    created_view = mock.MagicMock(spec=View, guid="guid-1")
    save_view_response = mock.MagicMock()
    save_view_response.assets_created.return_value = [created_view]
    save_view_response.assets_updated.return_value = []
    created_column = mock.MagicMock(spec=Column, guid="guid-col")
    save_column_response = mock.MagicMock()
    save_column_response.assets_created.return_value = [created_column]
    save_column_response.assets_updated.return_value = []
    client.save.side_effect = [save_view_response, save_column_response]
    client.asset.search.return_value = []

    result = upsert_technical_asset(client, _catalog_asset())

    assert result.asset_guid == "guid-1"
    assert result.column_guids == {"order_id": "guid-col"}


def test_upsert_technical_asset_raises_for_unsupported_type_name():
    client = mock.MagicMock()
    catalog_asset = _catalog_asset(asset_identity=AssetIdentity(type_name="MaterializedView", qualified_name="qn"))

    with pytest.raises(AssetUpsertError):
        upsert_technical_asset(client, catalog_asset)

    client.save.assert_not_called()


def test_upsert_technical_asset_wraps_atlan_api_error_on_save():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found_error()
    client.save.side_effect = RuntimeError("boom")

    with pytest.raises(AssetUpsertError):
        upsert_technical_asset(client, _catalog_asset())


def test_upsert_technical_asset_wraps_atlan_api_error_on_column_save():
    client = mock.MagicMock()

    def get_asset_side_effect(qualified_name, asset_type):
        raise _not_found_error()

    client.get_asset_by_qualified_name.side_effect = get_asset_side_effect

    created_view = mock.MagicMock(spec=View, guid="guid-1")
    save_view_response = mock.MagicMock()
    save_view_response.assets_created.return_value = [created_view]
    save_view_response.assets_updated.return_value = []

    client.save.side_effect = [save_view_response, RuntimeError("boom")]

    with pytest.raises(AssetUpsertError):
        upsert_technical_asset(client, _catalog_asset())


def test_upsert_technical_asset_archives_columns_removed_from_descriptor():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found_error()
    created_view = mock.MagicMock(spec=View, guid="guid-1")
    save_view_response = mock.MagicMock()
    save_view_response.assets_created.return_value = [created_view]
    save_view_response.assets_updated.return_value = []
    created_column = mock.MagicMock(spec=Column, guid="guid-col")
    save_column_response = mock.MagicMock()
    save_column_response.assets_created.return_value = [created_column]
    save_column_response.assets_updated.return_value = []
    client.save.side_effect = [save_view_response, save_column_response]

    stale_column = mock.MagicMock(spec=Column, guid="stale-guid")
    stale_column.name = "legacy_col"
    kept_column = mock.MagicMock(spec=Column, guid="guid-col")
    kept_column.name = "order_id"
    client.asset.search.return_value = [stale_column, kept_column]

    upsert_technical_asset(client, _catalog_asset())

    client.asset.delete_by_guid.assert_called_once_with(["stale-guid"])


def test_upsert_technical_asset_wraps_atlan_api_error_on_column_archival():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found_error()
    created_view = mock.MagicMock(spec=View, guid="guid-1")
    save_view_response = mock.MagicMock()
    save_view_response.assets_created.return_value = [created_view]
    save_view_response.assets_updated.return_value = []
    created_column = mock.MagicMock(spec=Column, guid="guid-col")
    save_column_response = mock.MagicMock()
    save_column_response.assets_created.return_value = [created_column]
    save_column_response.assets_updated.return_value = []
    client.save.side_effect = [save_view_response, save_column_response]
    client.asset.search.side_effect = RuntimeError("boom")

    with pytest.raises(AssetUpsertError):
        upsert_technical_asset(client, _catalog_asset())


# --- Unprovisioning: remove_technical_asset ---


def test_remove_technical_asset_is_noop_when_asset_missing():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found_error()

    result = remove_technical_asset(client, View, "qn", "archive")

    assert result is None
    client.asset.delete_by_guid.assert_not_called()
    client.asset.purge_by_guid.assert_not_called()


def test_remove_technical_asset_archives_asset_and_columns():
    client = mock.MagicMock()
    existing_view = mock.MagicMock(spec=View, guid="view-guid")
    client.get_asset_by_qualified_name.return_value = existing_view
    column = mock.MagicMock(spec=Column, guid="col-guid")
    client.asset.search.return_value = [column]

    result = remove_technical_asset(client, View, "qn", "archive")

    assert result == "view-guid"
    client.asset.delete_by_guid.assert_called_once_with(["col-guid", "view-guid"])
    client.asset.purge_by_guid.assert_not_called()


def test_remove_technical_asset_purges_asset_and_columns():
    client = mock.MagicMock()
    existing_view = mock.MagicMock(spec=View, guid="view-guid")
    client.get_asset_by_qualified_name.return_value = existing_view
    column = mock.MagicMock(spec=Column, guid="col-guid")
    client.asset.search.return_value = [column]

    result = remove_technical_asset(client, View, "qn", "purge")

    assert result == "view-guid"
    client.asset.purge_by_guid.assert_called_once_with(["col-guid", "view-guid"])
    client.asset.delete_by_guid.assert_not_called()


def test_remove_technical_asset_wraps_atlan_api_error():
    client = mock.MagicMock()
    existing_view = mock.MagicMock(spec=View, guid="view-guid")
    client.get_asset_by_qualified_name.return_value = existing_view
    client.asset.search.return_value = []
    client.asset.delete_by_guid.side_effect = RuntimeError("boom")

    with pytest.raises(AssetUpsertError):
        remove_technical_asset(client, View, "qn", "archive")


# --- ensure_source_system_link ---


def test_ensure_source_system_link_saves_idempotent_link():
    client = mock.MagicMock()
    asset_qn = "default/databricks/123/sales_catalog/sales_schema/orders_view"
    href = "https://adb-123.azuredatabricks.net/explore/data/catalog/schema/orders_view"

    ensure_source_system_link(client, View, asset_qn, "orders_view", href)

    client.save.assert_called_once()
    saved_link = client.save.call_args[0][0]
    assert isinstance(saved_link, Link)
    assert saved_link.qualified_name == f"{asset_qn}/Open in Databricks"
    assert saved_link.link == href
    # Regression: the referenced asset must be targeted by qualifiedName, never by
    # a fake guid — `asset_cls.updater()`'s `@init_guid` decorator unconditionally
    # assigns a random negative placeholder guid, which Atlan rejects with
    # "Referenced entity <fake-guid> is not found" (confirmed against a live
    # tenant). `trim_to_reference()` prefers `.guid` over `.qualified_name`, so a
    # `updater()`-built reference would silently break this link.
    referenced_asset = saved_link.asset
    assert not referenced_asset.guid
    assert referenced_asset.unique_attributes == {"qualifiedName": asset_qn}


def test_ensure_source_system_link_swallows_atlan_api_errors():
    client = mock.MagicMock()
    client.save.side_effect = RuntimeError("boom")

    # Must not raise: the link is a best-effort convenience, not part of the
    # documented Failure Matrix.
    ensure_source_system_link(client, View, "qn", "orders_view", "https://example.com")
