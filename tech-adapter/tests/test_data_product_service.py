from typing import Any
from unittest import mock

import pytest
from pyatlan.errors import NotFoundError
from pyatlan.model.assets import DataDomain
from pyatlan.model.assets import DataProduct as PyatlanDataProduct
from pyatlan.model.enums import DataProductCriticality, DataProductSensitivity, DataProductStatus, EntityStatus

from src.models.data_product_descriptor import DataProduct as DataProductDescriptor
from src.services.data_product_service import (
    DataProductUpsertError,
    DomainNotFoundError,
    _resolve_owner,
    build_asset_selection,
    compute_display_name,
    compute_slug,
    ensure_domain_exists,
    find_data_product_by_name_or_qualified_name,
    upsert_data_product,
)
from src.settings.atlan_settings import AtlanSettings

DOMAIN_QN = "default/domain/abcd1234"
DP_QN = f"{DOMAIN_QN}/product/risk-finance-production-v0"
DISPLAY_NAME = "Risk Finance-v0"


def _settings(**overrides: Any) -> AtlanSettings:
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com", oauth_client_id="id", oauth_client_secret="secret"
    )
    defaults.update(overrides)
    return AtlanSettings(**defaults)


def _build_data_product(**overrides) -> DataProductDescriptor:
    defaults = dict(
        id="urn:dmb:dp:finance:risk-finance:0",
        name="Risk Finance",
        description="A test data product",
        kind="dataproduct",
        domain="finance",
        version="0.1.0",
        environment="production",
        dataProductOwner="john.doe",
        ownerGroup="data-owners",
        devGroup="dev-team",
        specific={},
        components=[],
        tags=[],
    )
    defaults.update(overrides)
    return DataProductDescriptor(**defaults)


def _not_found() -> NotFoundError:
    return NotFoundError(mock.MagicMock(http_status_code=404, error_id="x", error_message="not found", user_action=""))  # noqa: E501


# --- compute_slug / compute_display_name ---


def test_compute_slug_derives_from_id_version_and_environment():
    data_product = _build_data_product()

    assert compute_slug(data_product) == "risk-finance-production-v0"


def test_compute_slug_raises_on_malformed_id():
    data_product = _build_data_product(id="not-a-urn")

    with pytest.raises(DataProductUpsertError):
        compute_slug(data_product)


def test_compute_display_name_includes_major_version():
    data_product = _build_data_product(name="Risk Finance", version="0.1.0")

    assert compute_display_name(data_product) == "Risk Finance-v0"


def test_compute_display_name_differs_across_major_versions():
    v0 = _build_data_product(version="0.9.0")
    v1 = _build_data_product(version="1.0.0")

    assert compute_display_name(v0) != compute_display_name(v1)
    assert compute_display_name(v0) == "Risk Finance-v0"
    assert compute_display_name(v1) == "Risk Finance-v1"


# --- ensure_domain_exists ---


def test_ensure_domain_exists_returns_qualified_name():
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})

    domain_qn = ensure_domain_exists(client, settings, "finance")

    assert domain_qn == DOMAIN_QN
    client.get_asset_by_qualified_name.assert_called_once()
    client.asset.search.assert_not_called()


def test_ensure_domain_exists_falls_back_to_by_name_search():
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": "Finance"})
    client.get_asset_by_qualified_name.side_effect = _not_found()
    matched_domain = mock.MagicMock(spec=DataDomain, qualified_name=DOMAIN_QN)
    client.asset.search.return_value = [matched_domain]

    domain_qn = ensure_domain_exists(client, settings, "finance")

    assert domain_qn == DOMAIN_QN


def test_ensure_domain_exists_raises_when_multiple_matches():
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": "Finance"})
    client.get_asset_by_qualified_name.side_effect = _not_found()
    client.asset.search.return_value = [
        mock.MagicMock(spec=DataDomain, qualified_name="default/domain/one"),
        mock.MagicMock(spec=DataDomain, qualified_name="default/domain/two"),
    ]

    with pytest.raises(DomainNotFoundError):
        ensure_domain_exists(client, settings, "finance")


def test_ensure_domain_exists_raises_when_not_found():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found()
    client.asset.search.return_value = []
    settings = _settings()

    with pytest.raises(DomainNotFoundError):
        ensure_domain_exists(client, settings, "finance")


def test_ensure_domain_exists_wraps_search_failure():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found()
    client.asset.search.side_effect = RuntimeError("boom")
    settings = _settings(domain_mapping={"finance": "Finance"})

    with pytest.raises(DomainNotFoundError):
        ensure_domain_exists(client, settings, "finance")


# --- _resolve_owner ---


def _user_response(*users: mock.MagicMock) -> mock.MagicMock:
    return mock.MagicMock(records=list(users))


def _group_response(*groups: mock.MagicMock) -> mock.MagicMock:
    return mock.MagicMock(records=list(groups))


def test_resolve_owner_resolves_user_prefix_email_to_atlan_username():
    client = mock.MagicMock()
    client.user.get_by_emails.return_value = _user_response(
        mock.MagicMock(email="jane.doe@agilelab.it", username="jane.doe.atlan")
    )
    data_product = _build_data_product(dataProductOwner="user:jane.doe_agilelab.it")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    client.user.get_by_emails.assert_called_once_with(emails=["jane.doe@agilelab.it"])
    assert owner_users == {"jane.doe.atlan"}
    assert owner_groups is None


def test_resolve_owner_leaves_owner_users_unset_when_no_exact_email_match():
    client = mock.MagicMock()
    client.user.get_by_emails.return_value = _user_response()  # no matches
    data_product = _build_data_product(dataProductOwner="user:jane.doe_agilelab.it")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    assert owner_users is None
    assert owner_groups is None


def test_resolve_owner_leaves_owner_users_unset_when_multiple_email_matches():
    client = mock.MagicMock()
    client.user.get_by_emails.return_value = _user_response(
        mock.MagicMock(email="jane.doe@agilelab.it", username="jane.doe.atlan"),
        mock.MagicMock(email="jane.doe@agilelab.it", username="jane.doe.atlan2"),
    )
    data_product = _build_data_product(dataProductOwner="user:jane.doe_agilelab.it")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    assert owner_users is None
    assert owner_groups is None


def test_resolve_owner_strips_tenant_prefix_from_user_identity():
    # Regression: Witboost now exports `user:` identities with a leading
    # `<tenant>/` segment (e.g. `user:default/jane.doe_agilelab.it`). Before the
    # fix, the tenant segment leaked into the email local-part, producing the
    # malformed `default/jane.doe@agilelab.it` and 0 Atlan user matches.
    client = mock.MagicMock()
    client.user.get_by_emails.return_value = _user_response(
        mock.MagicMock(email="jane.doe@agilelab.it", username="jane.doe.atlan")
    )
    data_product = _build_data_product(dataProductOwner="user:default/jane.doe_agilelab.it")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    client.user.get_by_emails.assert_called_once_with(emails=["jane.doe@agilelab.it"])
    assert owner_users == {"jane.doe.atlan"}
    assert owner_groups is None


def test_resolve_owner_strips_tenant_prefix_from_group_identity():
    client = mock.MagicMock()
    group_record = mock.MagicMock(alias="finance-owners")
    group_record.name = "finance-owners-internal"
    client.group.get_by_name.return_value = _group_response(group_record)
    data_product = _build_data_product(dataProductOwner="group:default/finance-owners")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    client.group.get_by_name.assert_called_once_with(alias="finance-owners")
    assert owner_users is None
    assert owner_groups == {"finance-owners-internal"}


def test_resolve_owner_resolves_group_alias_to_atlan_group_name():
    client = mock.MagicMock()
    group_record = mock.MagicMock(alias="finance-owners")
    group_record.name = "finance-owners-internal"
    client.group.get_by_name.return_value = _group_response(group_record)
    data_product = _build_data_product(dataProductOwner="group:finance-owners")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    client.group.get_by_name.assert_called_once_with(alias="finance-owners")
    assert owner_users is None
    assert owner_groups == {"finance-owners-internal"}


def test_resolve_owner_leaves_owner_groups_unset_when_no_exact_alias_match():
    client = mock.MagicMock()
    client.group.get_by_name.return_value = _group_response()  # no matches
    data_product = _build_data_product(dataProductOwner="group:finance-owners")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    assert owner_users is None
    assert owner_groups is None


def test_resolve_owner_returns_none_when_missing():
    client = mock.MagicMock()
    data_product = mock.MagicMock(dataProductOwner=None)

    assert _resolve_owner(client, data_product) == (None, None)
    client.user.get_by_emails.assert_not_called()
    client.group.get_by_name.assert_not_called()


def test_resolve_owner_falls_back_to_user_when_prefix_unrecognized():
    client = mock.MagicMock()
    client.user.get_by_emails.return_value = _user_response(
        mock.MagicMock(email="jane.doe@agilelab.it", username="jane.doe.atlan")
    )
    data_product = _build_data_product(dataProductOwner="jane.doe@agilelab.it")

    owner_users, owner_groups = _resolve_owner(client, data_product)

    client.user.get_by_emails.assert_called_once_with(emails=["jane.doe@agilelab.it"])
    assert owner_users == {"jane.doe.atlan"}
    assert owner_groups is None


# --- build_asset_selection ---


def test_build_asset_selection_raises_when_no_output_ports():
    with pytest.raises(DataProductUpsertError):
        build_asset_selection([])


def test_build_asset_selection_builds_or_query():
    request = build_asset_selection(["conn/db/schema/view1", "conn/db/schema/view2"])

    filter_clause = request.dsl.query.filter[0]
    assert filter_clause.minimum_should_match == 1
    assert len(filter_clause.should) == 2


# --- find_data_product_by_name_or_qualified_name ---


def test_find_data_product_by_name_or_qualified_name_returns_direct_match():
    client = mock.MagicMock()
    direct_match = mock.MagicMock(spec=PyatlanDataProduct, qualified_name=DP_QN)
    client.get_asset_by_qualified_name.return_value = direct_match

    result = find_data_product_by_name_or_qualified_name(client, DP_QN, DISPLAY_NAME, DOMAIN_QN)

    assert result is direct_match
    client.asset.search.assert_not_called()


def test_find_data_product_by_name_or_qualified_name_falls_back_to_name_search():
    client = mock.MagicMock()
    found_qn = f"{DOMAIN_QN}/product/old-slug"
    found_match = mock.MagicMock(spec=PyatlanDataProduct, qualified_name=found_qn)

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if qualified_name == DP_QN:
            raise _not_found()
        if qualified_name == found_qn:
            return found_match
        raise AssertionError(f"unexpected qualified_name lookup: {qualified_name}")

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = [found_match]

    result = find_data_product_by_name_or_qualified_name(client, DP_QN, DISPLAY_NAME, DOMAIN_QN)

    assert result is found_match


def test_find_data_product_by_name_or_qualified_name_returns_none_when_nothing_matches():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found()
    client.asset.search.return_value = []

    result = find_data_product_by_name_or_qualified_name(client, DP_QN, DISPLAY_NAME, DOMAIN_QN)

    assert result is None


def test_find_data_product_by_name_or_qualified_name_wraps_search_failure():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = _not_found()
    client.asset.search.side_effect = RuntimeError("boom")

    with pytest.raises(DataProductUpsertError):
        find_data_product_by_name_or_qualified_name(client, DP_QN, DISPLAY_NAME, DOMAIN_QN)


def test_find_data_product_by_name_or_qualified_name_wraps_generic_lookup_error():
    client = mock.MagicMock()
    client.get_asset_by_qualified_name.side_effect = RuntimeError("boom")

    with pytest.raises(DataProductUpsertError):
        find_data_product_by_name_or_qualified_name(client, DP_QN, DISPLAY_NAME, DOMAIN_QN)


# --- upsert_data_product ---


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_creates_new_product(mock_apply_custom_metadata):
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    # DataDomain lookup succeeds; DataProduct lookup finds nothing (and the
    # by-name fallback search also finds nothing) -> creator path.
    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        raise _not_found()

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = []
    client.user.get_by_emails.return_value = _user_response(
        mock.MagicMock(email="john.doe", username="john.doe.atlan")
    )

    created_asset = mock.MagicMock(guid="dp-guid-1")
    response = mock.MagicMock()
    response.assets_created.return_value = [created_asset]
    response.assets_updated.return_value = []
    client.save.return_value = response

    guid = upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
        witboost_link="https://witboost.example/marketplace/1",
    )

    assert guid == "dp-guid-1"
    client.save.assert_called_once()
    saved_dp = client.save.call_args[0][0]
    assert saved_dp.name == DISPLAY_NAME
    # `dataProductOwner` with no recognized prefix is resolved as a user email
    # and looked up for its Atlan-internal username.
    assert saved_dp.owner_users == {"john.doe.atlan"}
    mock_apply_custom_metadata.assert_called_once()
    call_args = mock_apply_custom_metadata.call_args.args
    values = call_args[4]
    assert values["Data Product URN"] == data_product.id
    assert values["Witboost Link"] == "https://witboost.example/marketplace/1"


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_sets_output_ports_relationship(mock_apply_custom_metadata):
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        raise _not_found()

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = []

    response = mock.MagicMock()
    response.assets_created.return_value = [mock.MagicMock(guid="dp-guid-1")]
    response.assets_updated.return_value = []
    client.save.return_value = response

    upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1", "op-guid-2"],
        output_port_qualified_names=["conn/db/schema/view1", "conn/db/schema/view2"],
    )

    saved_dp = client.save.call_args[0][0]
    # `outputPorts` (not just `daapOutputPortGuids`) is what actually populates the
    # Atlan UI's "Output Ports" tab (docs/technical-details.md §Phase 6).
    assert saved_dp.daap_output_port_guids == {"op-guid-1", "op-guid-2"}
    assert {ref.guid for ref in saved_dp.output_ports} == {"op-guid-1", "op-guid-2"}


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_updates_existing_product(mock_apply_custom_metadata):
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    existing_asset = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-existing", qualified_name=DP_QN, status=EntityStatus.ACTIVE
    )

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        return existing_asset

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name

    response = mock.MagicMock()
    response.assets_created.return_value = []
    response.assets_updated.return_value = []
    client.save.return_value = response

    guid = upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
    )

    assert guid == "dp-guid-existing"
    client.asset.search.assert_not_called()  # direct qualifiedName hit -> no by-name fallback needed
    saved_dp = client.save.call_args[0][0]
    assert saved_dp.qualified_name == DP_QN
    assert saved_dp.name == DISPLAY_NAME
    mock_apply_custom_metadata.assert_called_once()


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_reconciles_owner_on_update(mock_apply_custom_metadata):
    # Owner must be reconciled on every upsert, including updates of an already
    # existing DataProduct — a Data Product Owner change must propagate on
    # re-provisioning, not just at first creation.
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product(dataProductOwner="group:finance-owners")

    existing_asset = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-existing", qualified_name=DP_QN, status=EntityStatus.ACTIVE
    )

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        return existing_asset

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    group_record = mock.MagicMock(alias="finance-owners")
    group_record.name = "finance-owners-internal"
    client.group.get_by_name.return_value = _group_response(group_record)

    response = mock.MagicMock()
    response.assets_created.return_value = []
    response.assets_updated.return_value = [mock.MagicMock(guid="dp-guid-existing")]
    client.save.return_value = response

    upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
    )

    saved_dp = client.save.call_args[0][0]
    assert saved_dp.owner_users is None
    assert saved_dp.owner_groups == {"finance-owners-internal"}


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_restores_archived_product_and_marks_it_active(mock_apply_custom_metadata):
    # Regression: after a previous "archive"-strategy unprovision, re-provisioning
    # the same Data Product must not leave it "Archived" in Atlan despite
    # `/v1/provision` reporting success.
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    # Atlan assigned this DataProduct a different real qualifiedName than the one
    # freshly recomputed by `compute_slug()` (see `_find_existing_data_product`
    # docstring), so the direct qualifiedName lookup always misses and the
    # by-name (including archived) fallback search is what actually finds it.
    archived_qn = f"{DOMAIN_QN}/product/old-slug"
    refetch_results = iter(
        [
            mock.MagicMock(spec=PyatlanDataProduct, qualified_name=archived_qn, status=EntityStatus.DELETED),
            mock.MagicMock(
                spec=PyatlanDataProduct, guid="dp-guid-existing", qualified_name=archived_qn, status=EntityStatus.ACTIVE  # noqa: E501
            ),
        ]
    )

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        if qualified_name == DP_QN:
            raise _not_found()
        if qualified_name == archived_qn:
            return next(refetch_results)
        raise AssertionError(f"unexpected qualified_name lookup: {qualified_name}")

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = [mock.MagicMock(spec=PyatlanDataProduct, qualified_name=archived_qn)]
    client.asset.restore.return_value = True

    response = mock.MagicMock()
    response.assets_created.return_value = []
    response.assets_updated.return_value = [mock.MagicMock(guid="dp-guid-existing")]
    client.save.return_value = response

    guid = upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
    )

    assert guid == "dp-guid-existing"
    client.asset.restore.assert_called_once_with(PyatlanDataProduct, archived_qn)
    saved_dp = client.save.call_args[0][0]
    assert saved_dp.qualified_name == archived_qn
    assert saved_dp.status == EntityStatus.ACTIVE
    # Regression: `status` (EntityStatus) alone does not un-archive a DataProduct
    # in the Atlan Marketplace UI — the DataProduct-specific `daapStatus`/
    # `dataProductStatus` lifecycle badge (Active/Sunset/Archived/Draft) is a
    # completely separate field that must also be reset explicitly.
    assert saved_dp.daap_status == DataProductStatus.ACTIVE
    assert saved_dp.data_product_status == DataProductStatus.ACTIVE


def test_upsert_data_product_raises_when_atlan_restore_does_not_take_effect():
    # `client.asset.restore()` returns `False` (rather than raising) when the
    # restore silently didn't take effect — this must surface as an explicit
    # failure rather than silently leaving the DataProduct "Archived".
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    archived_qn = f"{DOMAIN_QN}/product/old-slug"

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        if qualified_name == DP_QN:
            raise _not_found()
        if qualified_name == archived_qn:
            return mock.MagicMock(spec=PyatlanDataProduct, qualified_name=archived_qn, status=EntityStatus.DELETED)
        raise AssertionError(f"unexpected qualified_name lookup: {qualified_name}")

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = [mock.MagicMock(spec=PyatlanDataProduct, qualified_name=archived_qn)]
    client.asset.restore.return_value = False

    with pytest.raises(DataProductUpsertError):
        upsert_data_product(
            client,
            settings,
            data_product,
            output_port_guids=["op-guid-1"],
            output_port_qualified_names=["conn/db/schema/view1"],
        )

    client.save.assert_not_called()


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_sets_criticality_and_sensitivity(mock_apply_custom_metadata):
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product(criticality="high", sensitivity="confidential")

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        raise _not_found()

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = []

    response = mock.MagicMock()
    response.assets_created.return_value = [mock.MagicMock(guid="dp-guid-1")]
    response.assets_updated.return_value = []
    client.save.return_value = response

    upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
    )

    saved_dp = client.save.call_args[0][0]
    assert saved_dp.daap_criticality == DataProductCriticality.HIGH
    assert saved_dp.daap_sensitivity == DataProductSensitivity.CONFIDENTIAL


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_leaves_criticality_and_sensitivity_unset_when_absent(mock_apply_custom_metadata):
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        raise _not_found()

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = []

    response = mock.MagicMock()
    response.assets_created.return_value = [mock.MagicMock(guid="dp-guid-1")]
    response.assets_updated.return_value = []
    client.save.return_value = response

    upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
    )

    saved_dp = client.save.call_args[0][0]
    assert saved_dp.daap_criticality is None
    assert saved_dp.daap_sensitivity is None


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_restores_archived_match_found_by_name(mock_apply_custom_metadata):
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    archived_qn = f"{DOMAIN_QN}/product/old-restored-slug"
    archived_match = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-archived", qualified_name=archived_qn, status=EntityStatus.DELETED
    )
    restored_match = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-archived", qualified_name=archived_qn, status=EntityStatus.ACTIVE
    )

    calls = {"count": 0}

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        if qualified_name == DP_QN:
            raise _not_found()
        if qualified_name == archived_qn:
            calls["count"] += 1
            # First lookup (right after the by-name search) still finds it archived;
            # the second lookup (after `client.asset.restore(...)`) finds it active.
            return archived_match if calls["count"] == 1 else restored_match
        raise AssertionError(f"unexpected qualified_name lookup: {qualified_name}")

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = [archived_match]

    response = mock.MagicMock()
    response.assets_created.return_value = []
    response.assets_updated.return_value = [mock.MagicMock(guid="dp-guid-archived")]
    client.save.return_value = response

    guid = upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
    )

    assert guid == "dp-guid-archived"
    client.asset.restore.assert_called_once_with(PyatlanDataProduct, archived_qn)
    saved_dp = client.save.call_args[0][0]
    assert saved_dp.qualified_name == archived_qn
    assert saved_dp.name == DISPLAY_NAME


@mock.patch("src.services.data_product_service.apply_custom_metadata")
def test_upsert_data_product_reuses_active_match_found_by_name_at_server_generated_qualified_name(
    mock_apply_custom_metadata,
):
    # Atlan assigns its own server-generated qualifiedName on DataProduct creation
    # (it does not honor the client-requested one), so on every subsequent
    # provisioning call the direct qualifiedName lookup misses and the by-name
    # fallback is the *normal* way the existing, still-ACTIVE DataProduct is found
    # again — it must be reused, not treated as a conflict.
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    server_generated_qn = f"{DOMAIN_QN}/product/IKqaMbC5HumAuX8DDFYdz"
    active_match = mock.MagicMock(
        spec=PyatlanDataProduct, guid="dp-guid-existing", qualified_name=server_generated_qn, status=EntityStatus.ACTIVE
    )

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        if qualified_name == DP_QN:
            raise _not_found()
        if qualified_name == server_generated_qn:
            return active_match
        raise AssertionError(f"unexpected qualified_name lookup: {qualified_name}")

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = [active_match]

    response = mock.MagicMock()
    response.assets_created.return_value = []
    response.assets_updated.return_value = [mock.MagicMock(guid="dp-guid-existing")]
    client.save.return_value = response

    guid = upsert_data_product(
        client,
        settings,
        data_product,
        output_port_guids=["op-guid-1"],
        output_port_qualified_names=["conn/db/schema/view1"],
    )

    assert guid == "dp-guid-existing"
    client.asset.restore.assert_not_called()  # already active, no restore needed
    saved_dp = client.save.call_args[0][0]
    assert saved_dp.qualified_name == server_generated_qn
    assert saved_dp.name == DISPLAY_NAME


def test_upsert_data_product_raises_when_domain_not_found():
    client = mock.MagicMock()
    settings = _settings()
    data_product = _build_data_product()

    client.get_asset_by_qualified_name.side_effect = _not_found()
    client.asset.search.return_value = []

    with pytest.raises(DomainNotFoundError):
        upsert_data_product(
            client,
            settings,
            data_product,
            output_port_guids=["op-guid-1"],
            output_port_qualified_names=["conn/db/schema/view1"],
        )


def test_upsert_data_product_wraps_save_failure():
    client = mock.MagicMock()
    settings = _settings(domain_mapping={"finance": DOMAIN_QN})
    data_product = _build_data_product()

    def get_asset_by_qualified_name(qualified_name, asset_type):  # noqa: ANN001
        if asset_type is DataDomain:
            return mock.MagicMock()
        raise _not_found()

    client.get_asset_by_qualified_name.side_effect = get_asset_by_qualified_name
    client.asset.search.return_value = []
    client.save.side_effect = RuntimeError("boom")

    with pytest.raises(DataProductUpsertError):
        upsert_data_product(
            client,
            settings,
            data_product,
            output_port_guids=["op-guid-1"],
            output_port_qualified_names=["conn/db/schema/view1"],
        )
