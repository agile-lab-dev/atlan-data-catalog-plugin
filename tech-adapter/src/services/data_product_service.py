from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.errors import NotFoundError
from pyatlan.model.assets import Asset, DataDomain, DataProduct
from pyatlan.model.enums import (
    DataProductCriticality,
    DataProductSensitivity,
    DataProductStatus,
    EntityStatus,
)
from pyatlan.model.fluent_search import FluentSearch
from pyatlan.model.search import Bool, IndexSearchRequest

from src.models.data_product_descriptor import DataProduct as DataProductDescriptor
from src.services.custom_metadata_service import apply_custom_metadata
from src.settings.atlan_settings import AtlanSettings


class DomainNotFoundError(Exception):
    """Raised when the pre-provisioned Atlan DataDomain cannot be found (docs/technical-details.md §Phase 6, step 9)."""  # noqa: E501

    code = "DOMAIN_NOT_FOUND"


class DataProductUpsertError(Exception):
    """Raised when the Atlan DataProduct cannot be upserted due to an Atlan API error (docs/technical-details.md §Phase 6)."""  # noqa: E501

    code = "DATAPRODUCT_UPSERT_FAILED"


def compute_slug(data_product: DataProductDescriptor) -> str:
    """
    Derives the Atlan DataProduct slug from the Witboost Data Product descriptor
    (docs/technical-details.md §Phase 6, step 1): `<normalized-dp-name>-<environment>-v<major-version>`.

    `normalized_dp_name` is the second-to-last colon-separated segment of the
    descriptor `id` (e.g. `urn:dmb:dp:finance:risk-finance:0` -> `risk-finance`);
    `major_version` is the leading numeric segment of `version` (e.g. `0.1.0` -> `0`).
    """  # noqa: E501
    segments = data_product.id.split(":")
    if len(segments) < 2:
        raise DataProductUpsertError(f"Cannot derive slug from malformed Data Product id '{data_product.id}'")
    normalized_name = segments[-2]
    major_version = data_product.version.split(".")[0]
    return f"{normalized_name}-{data_product.environment}-v{major_version}"


def compute_display_name(data_product: DataProductDescriptor) -> str:
    """
    Derives the Atlan DataProduct display `name` (docs/technical-details.md §Phase 6,
    step 3): `<data_product.name>-v<major_version>`.

    Atlan enforces domain-scoped uniqueness on a DataProduct's `name` independently
    of its `qualifiedName`. Different **major** versions of the same Data Product
    already get a different `qualifiedName` (via `compute_slug()`), but without the
    version suffix here they would collide on `name` if provisioned concurrently
    (e.g. an old major version still active while a new one is deployed) — the
    suffix keeps every major version coexistable as a distinct Atlan DataProduct.
    """  # noqa: E501
    major_version = data_product.version.split(".")[0]
    return f"{data_product.name}-v{major_version}"


def ensure_domain_exists(client: AtlanClient, settings: AtlanSettings, domain: str) -> str:
    """
    Resolves and verifies the Atlan DataDomain for the given Witboost domain
    (docs/technical-details.md §Phase 6, step 9). DataDomains are pre-provisioned,
    like Connections — the plugin never creates them.

    The configured `domain_mapping` value may be either the DataDomain's real
    `qualifiedName` (e.g. `"default/domain/2CZLqmfGjKmzHBYX0MwMn/super"`) or its
    human-readable display `name` as shown in the Atlan UI (e.g. `"Pre Sales"`) —
    operators configuring `domain_mapping` typically only know the UI name, not
    the internal qualifiedName. Resolution tries the configured value as a
    `qualifiedName` first (exact, unambiguous); if that misses, it falls back to
    a by-name search.

    Returns:
        str: the Atlan DataDomain qualifiedName.

    Raises:
        DomainNotFoundError: the configured DataDomain does not exist in Atlan
            (as either a qualifiedName or a name), or more than one DataDomain
            shares that name (ambiguous — Atlan does not enforce domain name
            uniqueness, so the qualifiedName must be configured explicitly in
            that case).
    """  # noqa: E501
    configured = settings.domain_qualified_name(domain)
    try:
        client.get_asset_by_qualified_name(configured, DataDomain)
        return configured
    except NotFoundError:
        pass

    try:
        matches = [
            asset
            for asset in FluentSearch()
            .where(Asset.TYPE_NAME.eq("DataDomain"))
            .where(Asset.NAME.eq(configured))
            .execute(client=client)
            if isinstance(asset, DataDomain)
        ]
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise DomainNotFoundError(f"Failed to search for Atlan DataDomain named '{configured}': {exc}") from exc

    if not matches:
        raise DomainNotFoundError(f"Atlan DataDomain '{configured}' not found (tried both qualifiedName and name)")
    if len(matches) > 1:
        raise DomainNotFoundError(
            f"Multiple Atlan DataDomains named '{configured}' found — configure `domain_mapping` with the "
            "unambiguous qualifiedName instead of the display name for this domain"
        )
    resolved_qn = matches[0].qualified_name
    if resolved_qn is None:
        raise DomainNotFoundError(f"Atlan DataDomain named '{configured}' has no qualifiedName")
    return resolved_qn


def build_asset_selection(asset_qualified_names: list[str]) -> IndexSearchRequest:
    """
    Builds the Output Port asset-selection DSL for a DataProduct
    (docs/technical-details.md §Phase 6, step 3): an OR-query across the
    qualifiedNames of all Output Port assets belonging to this Data Product.

    Rebuilt from scratch on every call, per the documented warning that manual UI
    edits to a DataProduct's asset selection would otherwise freeze a stale DSL.

    The OR terms must be nested inside a single `where()` filter clause (not
    `where_some`/`min_somes` at the top level) — pyatlan's DataProduct DSL
    translator requires a non-empty `filter` clause on the outer bool query.
    """  # noqa: E501
    if not asset_qualified_names:
        raise DataProductUpsertError("Cannot build asset selection DSL: no Output Port assets resolved")
    terms = [Asset.QUALIFIED_NAME.eq(qn) for qn in asset_qualified_names]
    return FluentSearch().where(Bool(should=terms, minimum_should_match=1)).to_request()


def _search_data_product_by_name(client: AtlanClient, name: str, domain_qn: str) -> DataProduct | None:
    try:
        results = list(
            FluentSearch()
            .select(include_archived=True)
            .where(Asset.TYPE_NAME.eq("DataProduct"))
            .where(Asset.NAME.eq(name))
            .where(Asset.QUALIFIED_NAME.startswith(f"{domain_qn}/"))
            .execute(client=client)
        )
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise DataProductUpsertError(
            f"Failed to search for an existing DataProduct named '{name}' in domain '{domain_qn}': {exc}"
        ) from exc

    match = next((asset for asset in results if isinstance(asset, DataProduct)), None)
    if match is None or match.qualified_name is None:
        return None
    return client.get_asset_by_qualified_name(match.qualified_name, DataProduct)


def find_data_product_by_name_or_qualified_name(
    client: AtlanClient, qualified_name: str, name: str, domain_qn: str
) -> DataProduct | None:
    """
    Public: looks up a DataProduct by `qualified_name`, falling back to a by-name
    search scoped to the domain (including archived assets) if not found.

    Atlan enforces domain-scoped uniqueness on a DataProduct's `name`
    independently of its `qualifiedName` (ATLAS-400-00-029 "... already exists in
    the domain"). A DataProduct archived by a previous "archive"-strategy
    unprovision (docs/technical-details.md §Unprovisioning) may end up reachable
    only via this by-name fallback (e.g. if it was originally created/restored at
    a qualifiedName that no longer matches the freshly recomputed one) — reused by
    `unprovision_data_product()` to find the right entity to archive/purge,
    instead of silently no-op'ing on a qualifiedName mismatch. NOTE: unlike
    `_find_existing_data_product()` (used by provisioning), this does not
    distinguish an archived vs. still-active by-name match — safe for
    unprovisioning (removing whichever entity currently holds this name), but not
    reused for provisioning's create/update decision (see `_find_existing_data_product`
    for why an active by-name match must be treated as a conflict there, not
    silently reused).
    """  # noqa: E501
    try:
        return client.get_asset_by_qualified_name(qualified_name, DataProduct)
    except NotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise DataProductUpsertError(f"Failed to look up existing DataProduct '{qualified_name}': {exc}") from exc

    return _search_data_product_by_name(client, name, domain_qn)


def _find_existing_data_product(
    client: AtlanClient, qualified_name: str, name: str, domain_qn: str
) -> DataProduct | None:
    match: DataProduct | None
    try:
        match = client.get_asset_by_qualified_name(qualified_name, DataProduct)
    except NotFoundError:
        match = _search_data_product_by_name(client, name, domain_qn)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise DataProductUpsertError(f"Failed to look up existing DataProduct '{qualified_name}': {exc}") from exc

    if match is None or match.qualified_name is None:
        return None

    if match.status != EntityStatus.DELETED:
        # Reused regardless of whether it was found directly by `qualified_name`
        # or via the by-name fallback. Atlan does not honor the client-requested
        # `qualifiedName` when creating a DataProduct (it assigns its own
        # server-generated identifier instead), so the by-name fallback is the *normal* way this same DataProduct is
        # found again on every subsequent provisioning call, not just an edge
        # case. It is safe to reuse: `name` already embeds the major version
        # (`compute_display_name()`), so a match found by that exact name is
        # guaranteed to be the same major version of this same Data Product —
        # Atlan's own domain-scoped name uniqueness rules out any other
        # unrelated, still-active DataProduct sharing this exact name.
        return match

    # A DataProduct archived by a previous "archive"-strategy unprovision
    # (docs/technical-details.md §Unprovisioning) still occupies its `name` even
    # though it may no longer be found by `qualifiedName` — without restoring it
    # here, re-provisioning the same Data Product after an unprovision fails
    # instead of being idempotent.
    logger.info("Restoring archived DataProduct '{}' so re-provisioning stays idempotent", match.qualified_name)
    try:
        restored = client.asset.restore(DataProduct, match.qualified_name)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise DataProductUpsertError(f"Failed to restore archived DataProduct '{match.qualified_name}': {exc}") from exc  # noqa: E501
    # `restore()` returns `False` (rather than raising) if the restore silently
    # did not take effect (e.g. the entity vanished between the lookup above and
    # the restore call) — without this check, `upsert_data_product` would
    # otherwise happily update the still-DELETED asset next (its `updater()`
    # never sets `status`, so a no-op restore would leave the DataProduct
    # permanently "Archived" in Atlan despite `/v1/provision` reporting success).
    if not restored:
        raise DataProductUpsertError(f"Failed to restore archived DataProduct '{match.qualified_name}': Atlan reported the restore did not take effect")  # noqa: E501
    return client.get_asset_by_qualified_name(match.qualified_name, DataProduct)


def _extract_guid(response, existing: DataProduct | None) -> str:  # noqa: ANN001
    created = response.assets_created(DataProduct)
    if created:
        return created[0].guid
    updated = response.assets_updated(DataProduct)
    if updated:
        return updated[0].guid
    if existing is not None:
        # Atlan performed a no-op save (nothing actually changed); reuse the GUID
        # already resolved by the pre-save lookup.
        return existing.guid
    raise DataProductUpsertError("Atlan did not return the created/updated DataProduct")


def _resolve_username_by_email(client: AtlanClient, email: str) -> str | None:
    """
    Looks up the Atlan-internal `username` for the given email address
    (see docs/technical-details.md, "Phase 6: DataProduct Upsert & Linking"):
    Atlan's `owner_users` field requires its own `username`, not an email
    address, so the email derived from `dataProductOwner` must be resolved to
    a real Atlan user first.

    Uses `client.user.get_by_emails()` (exact list match) and then re-checks
    the returned records' `email` for an exact match, since the underlying
    Atlan search is not guaranteed to be a strict equality filter.

    Returns None (logging a warning) if zero or more than one Atlan user
    matches the email — ownership is left unset rather than guessed, since a
    wrong guess would be worse than no owner at all.
    """
    try:
        response = client.user.get_by_emails(emails=[email])
        records = (response.records if response else None) or []
        matches = [record for record in records if record.email == email]
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure must not abort the whole upsert
        logger.warning("Failed to look up Atlan user by email '{}': {}", email, exc)
        return None

    if len(matches) != 1:
        logger.warning(
            "Expected exactly one Atlan user with email '{}', found {}; DataProduct owner_users left unset",
            email,
            len(matches),
        )
        return None
    return matches[0].username


def _resolve_group_name_by_alias(client: AtlanClient, alias: str) -> str | None:
    """
    Looks up the Atlan-internal group `name` for the given UI-displayed group
    alias. Like users, Atlan's `owner_groups` requires the group's internal
    `name`, not its display `alias` as configured in `dataProductOwner`'s
    `group:` prefix.

    Uses `client.group.get_by_name()` (a contains/`ilike` search) and then
    re-checks the returned records' `alias` for an exact match.

    Returns None (logging a warning) if zero or more than one Atlan group
    matches the alias — ownership is left unset rather than guessed.
    """
    try:
        response = client.group.get_by_name(alias=alias)
        records = (response.records if response else None) or []
        matches = [record for record in records if record.alias == alias]
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure must not abort the whole upsert
        logger.warning("Failed to look up Atlan group by alias '{}': {}", alias, exc)
        return None

    if len(matches) != 1:
        logger.warning(
            "Expected exactly one Atlan group with alias '{}', found {}; DataProduct owner_groups left unset",
            alias,
            len(matches),
        )
        return None
    return matches[0].name


def _strip_tenant_prefix(identity: str) -> str:
    """
    Strips the leading `<tenant>/` segment that Witboost may prepend to exported
    `user:`/`group:` identities (e.g. `default/jane.doe_agilelab.it`). Identities
    without a tenant segment (e.g. `jane.doe_agilelab.it`) are left unchanged.
    Only the first `/` is significant — anything before it is the tenant,
    everything after it is the actual local/group identity — so this is safe
    even if the identity itself were ever to contain a `/`.
    """
    _, _, rest = identity.partition("/")
    return rest if rest else identity


def _resolve_owner(
    client: AtlanClient, data_product: DataProductDescriptor
) -> tuple[set[str] | None, set[str] | None]:
    """
    Resolves Atlan DataProduct ownership from `dataProduct.dataProductOwner`.

    - `user:[<tenant>/]<local_part>_<domain>` -> the `user:` prefix and any
      leading `<tenant>/` segment (see `_strip_tenant_prefix`) are stripped,
      then the LAST underscore is replaced with `@` to build an email address
      (Witboost user identities are exported with the mail domain's
      dot-separated `@` replaced by `_`, e.g. `user:jane.doe_agilelab.it` ->
      `jane.doe@agilelab.it`; splitting on the *last* underscore keeps this
      correct even if the local part itself contains underscores). That email
      is then resolved to Atlan's own `username` via `_resolve_username_by_email`
      (Atlan's `owner_users` field expects a `username`, not an email address)
      -> owner_users={"<username>"}, or None if no unambiguous Atlan user match
      is found.
    - `group:[<tenant>/]<group>` -> the `group:` prefix and any leading
      `<tenant>/` segment are stripped, and the alias is resolved to Atlan's
      internal group `name` via `_resolve_group_name_by_alias`
      -> owner_groups={"<name>"}, or None if no unambiguous match is found.
    - Missing value -> (None, None), clearing ownership on update.
    - Unrecognized prefix -> treated as an email address as-is (same
      username-resolution flow as the `user:` case), with a warning logged.
    """
    owner = data_product.dataProductOwner
    if not owner:
        return None, None

    if owner.startswith("user:"):
        identity = _strip_tenant_prefix(owner.removeprefix("user:"))
        if "_" in identity:
            local_part, domain = identity.rsplit("_", 1)
            identity = f"{local_part}@{domain}"
        username = _resolve_username_by_email(client, identity)
        return ({username} if username else None), None

    if owner.startswith("group:"):
        alias = _strip_tenant_prefix(owner.removeprefix("group:"))
        group_name = _resolve_group_name_by_alias(client, alias)
        return None, ({group_name} if group_name else None)

    logger.warning("dataProductOwner '{}' has no recognized 'user:'/'group:' prefix; treating as a user", owner)
    username = _resolve_username_by_email(client, owner)
    return ({username} if username else None), None


def upsert_data_product(
    client: AtlanClient,
    settings: AtlanSettings,
    data_product: DataProductDescriptor,
    output_port_guids: list[str],
    output_port_qualified_names: list[str],
    witboost_link: str | None = None,
) -> str:
    """
    Phase 6: creates or updates the Atlan DataProduct for the given Witboost Data
    Product and links it to its resolved Output Port assets
    (docs/technical-details.md §Phase 6: DataProduct Upsert & Linking).

    Returns:
        str: the Atlan GUID of the upserted DataProduct.

    Raises:
        DomainNotFoundError: the configured DataDomain does not exist in Atlan.
        DataProductUpsertError: any other Atlan API failure while upserting the
            DataProduct or applying its Custom Metadata.
    """  # noqa: E501
    domain_qn = ensure_domain_exists(client, settings, data_product.domain)
    slug = compute_slug(data_product)
    dp_qn = f"{domain_qn}/product/{slug}"
    display_name = compute_display_name(data_product)

    existing = _find_existing_data_product(client, dp_qn, display_name, domain_qn)
    asset_selection = build_asset_selection(output_port_qualified_names)

    logger.info("Upserting DataProduct '{}' ({})", dp_qn, "update" if existing else "create")
    try:
        if existing is not None:
            # Use the existing asset's own qualifiedName (not the freshly
            # recomputed `dp_qn`) — it is the authoritative identity when
            # `existing` was resolved via the by-name/restore fallback in
            # `_find_existing_data_product`, which may not match `dp_qn` exactly.
            data_product_asset = DataProduct.updater(
                qualified_name=existing.qualified_name, name=display_name, asset_selection=asset_selection
            )
            # Defensive, belt-and-suspenders un-archive: `_find_existing_data_product`
            # already calls `client.asset.restore()` when `existing` was archived, but
            # `DataProduct.updater()` never sets `status` on its own, so if the
            # separate restore call ever silently failed to take effect (e.g. Atlan
            # eventual consistency), this save would otherwise leave the DataProduct
            # permanently "Archived" despite `/v1/provision` reporting success. Always
            # setting `status=ACTIVE` here makes this upsert idempotent/self-healing
            # regardless of the restore call's own reliability.
            data_product_asset.status = EntityStatus.ACTIVE
            # `status` (EntityStatus) is the generic Atlan soft-delete flag and is
            # NOT what the Marketplace UI shows as the DataProduct's own lifecycle
            # badge (e.g. "Published"/"Archived") — that badge is driven by the
            # DataProduct-specific `daapStatus`/`dataProductStatus` attribute
            # (`DataProductStatus`: Active/Sunset/Archived/Draft), a completely
            # separate field that `client.asset.restore()`/`DataProduct.updater()`
            # never touch. Without resetting it here too, a previously-archived
            # DataProduct would keep showing "Archived" in the UI forever, even
            # after its underlying entity `status` is correctly restored to ACTIVE.
            data_product_asset.daap_status = DataProductStatus.ACTIVE
            data_product_asset.data_product_status = DataProductStatus.ACTIVE
        else:
            data_product_asset = DataProduct.creator(
                name=slug,
                domain_qualified_name=domain_qn,
                asset_selection=asset_selection,
            )
            # `name` (not `slug`) is what Atlan enforces domain-scoped uniqueness on
            # and what the UI displays; it includes the major version suffix (see
            # `compute_display_name()`) so different major versions of the same Data
            # Product can coexist as distinct DataProducts instead of colliding.
            data_product_asset.name = display_name

        # Owner is reconciled on every upsert (create AND update), not just on
        # creation — a Data Product Owner change must propagate to an already
        # existing Atlan DataProduct on re-provisioning.
        owner_users, owner_groups = _resolve_owner(client, data_product)
        data_product_asset.owner_users = owner_users
        data_product_asset.owner_groups = owner_groups

        # Criticality/Sensitivity map to Atlan's native DataProduct attributes
        if data_product.criticality is not None:
            data_product_asset.daap_criticality = DataProductCriticality(data_product.criticality.value)
        if data_product.sensitivity is not None:
            data_product_asset.daap_sensitivity = DataProductSensitivity(data_product.sensitivity.value)

        data_product_asset.daap_output_port_guids = set(output_port_guids)
        # The `outputPorts` relationship (not just the `daapOutputPortGuids` keyword
        # field) is what actually populates the DataProduct's "Output Ports" tab in
        # the Atlan UI; `daapOutputPortGuids` alone only drives search/filtering and
        # leaves the asset visible solely under the generic "Assets" tab.
        data_product_asset.output_ports = [Asset.ref_by_guid(guid) for guid in output_port_guids]

        response = client.save(data_product_asset)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise DataProductUpsertError(f"Failed to upsert DataProduct '{dp_qn}': {exc}") from exc

    guid = _extract_guid(response, existing)

    values: dict[str, str | None] = {
        "Data Product URN": data_product.id,
        "Data Product Version": data_product.version,
        "Domain": data_product.domain,
        "Environment": data_product.environment,
        "Data Product Owner": data_product.dataProductOwner,
        "Dev Group": data_product.devGroup,
        "Witboost Link": witboost_link,
        "Last Synced At": datetime.now(timezone.utc).isoformat(),
    }
    apply_custom_metadata(client, settings, guid, DataProduct, values)

    return guid
