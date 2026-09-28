from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.model.assets import Column
from pyatlan.model.assets import DataProduct as AtlanDataProduct

from src.models.api_models import Info, ProvisioningStatus, Status1, SystemErr
from src.models.data_product_descriptor import DataProduct, OpenMetadataTagLabel
from src.resolvers.registry import OutputPortResolverRegistry
from src.services.asset_service import (
    ASSET_TYPES,
    atlan_link,
    ensure_parent_hierarchy,
    ensure_source_system_link,
    upsert_technical_asset,
)
from src.services.custom_metadata_service import apply_custom_metadata
from src.services.data_product_service import upsert_data_product
from src.services.glossary_service import ensure_business_term, resolve_glossary_name
from src.services.metadata_service import ensure_custom_metadata_def
from src.services.term_association_service import associate_business_terms
from src.settings.atlan_settings import AtlanSettings

# Open configuration gap: no config key exists yet for the Witboost Marketplace
# base URL needed to build the "Witboost Link" Custom Metadata attribute value.
# Left `None` (cleared) until that decision is made, rather than blocking
# provisioning entirely on it.
_WITBOOST_LINK: str | None = None


def provision_data_product(
    client: AtlanClient,
    settings: AtlanSettings,
    data_product: DataProduct,
    component_id: str,
) -> ProvisioningStatus | SystemErr:
    """
    Runs the full 7-phase provisioning flow documented in
    docs/technical-details.md §Provisioning against the given Data Product
    descriptor, synchronously (docs/technical-details.md §Provisioning: "`POST
    /v1/provision` ... execute synchronously: all phases run within the request").

    Per docs/HLD.md's provisioning flow (Phase 0: "extract Data Product metadata and
    ALL Output Ports"), every Output Port in the descriptor is (re-)processed on
    every call — this is a Data Catalog Plugin, not a per-component Tech Adapter,
    and Phase 6's asset-selection DSL / `daap_output_port_guids` must always be
    rebuilt from the complete, current set of Output Ports (docs/technical-details.md
    §Phase 6, step 2). `component_id` is used only for logging/traceability, not to
    filter which Output Ports are processed.

    Returns:
        ProvisioningStatus(status=COMPLETED, ...): every phase succeeded (Output
            Ports with an unsupported technology are silently skipped, matching
            `validate`'s contract).
        SystemErr: a phase failed; `error` carries `"<CODE>: <message>"`, `<CODE>`
            being one of the documented Failure Matrix codes.
    """  # noqa: E501
    logger.info("Provisioning Data Product '{}' (triggered by component '{}')", data_product.id, component_id)

    try:
        # Phase 2: prerequisite metadata definitions.
        ensure_custom_metadata_def(client, settings)

        glossary_name = resolve_glossary_name(settings, data_product.domain)
        dp_root_tag_fqns, tag_fqns_by_output_port, tag_fqns_by_column = _collect_tag_fqns(data_product)
        all_tag_fqns: set[str] = set(dp_root_tag_fqns)
        all_tag_fqns.update(*tag_fqns_by_output_port.values())
        for per_op_columns in tag_fqns_by_column.values():
            all_tag_fqns.update(*per_op_columns.values())

        terms_by_fqn = {
            tag_fqn: ensure_business_term(client, settings, glossary_name, tag_fqn)
            for tag_fqn in sorted(all_tag_fqns)
        }

        registry = OutputPortResolverRegistry()
        now = datetime.now(timezone.utc).isoformat()

        public_info: dict[str, Any] = {}
        output_port_guids: list[str] = []
        output_port_qualified_names: list[str] = []

        for output_port in data_product.get_output_ports():
            catalog_asset = registry.resolve(output_port, settings, data_product.environment)
            if catalog_asset is None:
                continue  # unsupported technology, skip silently (Phase 1, step 2)

            asset_type = ASSET_TYPES[catalog_asset.asset_identity.type_name]

            # Phase 3
            ensure_parent_hierarchy(client, catalog_asset)

            # Phase 4
            upsert_result = upsert_technical_asset(client, catalog_asset)

            # Best-effort: deep link to the table/view in its source technology
            # (e.g. Databricks), when the enriching Tech Adapter populated it.
            source_table_url = output_port.source_table_url()
            if source_table_url:
                ensure_source_system_link(
                    client,
                    asset_type,
                    catalog_asset.asset_identity.qualified_name,
                    catalog_asset.name,
                    source_table_url,
                )

            # Phase 5: Custom Metadata on the technical asset and each of its columns
            shared_values: dict[str, str | None] = {
                "Domain": data_product.domain,
                "Environment": data_product.environment,
                "Output Port URN": output_port.id,
                "Witboost Link": _WITBOOST_LINK,
                "Last Synced At": now,
            }
            apply_custom_metadata(client, settings, upsert_result.asset_guid, asset_type, shared_values)
            for column_guid in upsert_result.column_guids.values():
                apply_custom_metadata(client, settings, column_guid, Column, shared_values)

            # Phase 7: Business Term association (component-root -> asset, per-column -> column)
            op_terms = [terms_by_fqn[fqn] for fqn in tag_fqns_by_output_port.get(output_port.id, [])]
            associate_business_terms(client, asset_type, upsert_result.asset_guid, op_terms)

            for column_name, column_fqns in tag_fqns_by_column.get(output_port.id, {}).items():
                term_column_guid = upsert_result.column_guids.get(column_name)
                if term_column_guid is None:
                    continue
                column_terms = [terms_by_fqn[fqn] for fqn in column_fqns]
                associate_business_terms(client, Column, term_column_guid, column_terms)

            asset_qn = catalog_asset.asset_identity.qualified_name

            output_port_guids.append(upsert_result.asset_guid)
            output_port_qualified_names.append(asset_qn)
            public_info[output_port.id] = atlan_link(settings, upsert_result.asset_guid, catalog_asset.name)

        if output_port_guids:
            # Phase 6
            dp_guid = upsert_data_product(
                client,
                settings,
                data_product,
                output_port_guids=output_port_guids,
                output_port_qualified_names=output_port_qualified_names,
                witboost_link=_WITBOOST_LINK,
            )

            # Phase 7 (DataProduct-level: DP-root tagFQNs)
            dp_terms = [terms_by_fqn[fqn] for fqn in dp_root_tag_fqns]
            associate_business_terms(client, AtlanDataProduct, dp_guid, dp_terms)

            public_info[data_product.id] = atlan_link(settings, dp_guid, data_product.name)
        else:
            logger.info(
                "No supported Output Port produced a catalog asset for Data Product '{}' "
                "— skipping DataProduct upsert (Phase 6)",
                data_product.id,
            )

        return ProvisioningStatus(
            status=Status1.COMPLETED,
            result=f"Data Product '{data_product.name}' provisioned successfully in Atlan",
            info=Info(publicInfo=public_info, privateInfo=public_info),
        )

    except Exception as exc:  # noqa: BLE001 - translate any phase failure into the documented SystemErr code
        code = getattr(exc, "code", "UNEXPECTED_ERROR")
        logger.error("Provisioning failed for Data Product '{}': {}: {}", data_product.id, code, exc)
        return SystemErr(error=f"{code}: {exc}")


def _tag_fqns(labels: list[OpenMetadataTagLabel] | None) -> list[str]:
    return [label.tagFQN for label in (labels or [])]


def _collect_tag_fqns(
    data_product: DataProduct,
) -> tuple[list[str], dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """
    Extracts every `tagFQN` referenced in the descriptor (docs/technical-details.md
    §Phase 2, step 2), grouped by scope: Data Product root, per-Output-Port
    (component root), and per-column (`dataContract.schema[].tags`).

    Note: the current descriptor model (`models/data_product_descriptor.py`) only
    exposes `tags[].tagFQN` — there is no `businessConcepts` field anywhere in the
    parsed model, even though docs/technical-details.md also mentions
    `businessConcepts[].tagFQN` as a term source. Only the `tags` paths that
    actually exist in the model are implemented.
    """  # noqa: E501
    dp_root = _tag_fqns(data_product.tags)

    by_output_port: dict[str, list[str]] = {}
    by_column: dict[str, dict[str, list[str]]] = {}
    for output_port in data_product.get_output_ports():
        by_output_port[output_port.id] = _tag_fqns(output_port.tags)
        columns = output_port.dataContract.schema_ or []
        by_column[output_port.id] = {
            column.name: _tag_fqns(column.tags) for column in columns if column.tags
        }

    return dp_root, by_output_port, by_column
