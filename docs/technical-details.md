# Technical Details — Atlan Data Catalog Plugin

This document provides the technical implementation details for the Atlan Data Catalog Plugin. It complements the [High Level Design](HLD.md) with API-level interactions, domain model, resolver design, entity mapping rules, and error codes.

- [Module Layout](#module-layout)
- [Descriptor Model](#descriptor-model)
- [Provisioning](#provisioning)
  - [Phase 0: Descriptor Parsing](#phase-0-descriptor-parsing)
  - [Phase 1: Output Port Resolution](#phase-1-output-port-resolution)
  - [Phase 2: Prerequisite Metadata Definitions](#phase-2-prerequisite-metadata-definitions)
  - [Phase 3: Parent Hierarchy Upsert](#phase-3-parent-hierarchy-upsert)
  - [Phase 4: Technical Asset Upsert](#phase-4-technical-asset-upsert)
  - [Phase 5: Apply Custom Metadata](#phase-5-apply-custom-metadata)
  - [Phase 6: DataProduct Upsert & Linking](#phase-6-dataproduct-upsert--linking)
  - [Phase 7: Associate Business Terms](#phase-7-associate-business-terms)
- [Unprovisioning](#unprovisioning)
- [Validation](#validation)
- [Output Port Resolver Design](#output-port-resolver-design)
  - [Resolver Interface](#resolver-interface)
  - [Databricks Resolver](#databricks-resolver)
  - [Adding a New Technology](#adding-a-new-technology)
- [Domain Model](#domain-model)
- [Entity Identity and qualifiedName](#entity-identity-and-qualifiedname)
- [Entity Mapping Reference](#entity-mapping-reference)
- [Business Terms](#business-terms)
- [Atlan Client Initialization](#atlan-client-initialization)
- [Configuration](#configuration)
- [Error Response Structure](#error-response-structure)
- [Failure Matrix](#failure-matrix)

---

## Module Layout

```text
src/
  main.py                        # FastAPI routes, request-logging middleware
  app_config.py                  # FastAPI app instance (title, description, version)
  check_return_type.py           # Maps a returned Pydantic model to its HTTP status code
  dependencies.py                # FastAPI dependencies that unpack/parse each request's descriptor
  clients/
    atlan_client_factory.py      # AtlanSettings/AtlanClient dependency providers (cached)
  models/
    api_models.py                # API contracts: ProvisioningRequest, ProvisioningStatus, ValidationResult, ValidationError, SystemErr, Info, DescriptorKind, EntityReference, ...
    data_product_descriptor.py   # Pydantic descriptor models (DataProduct, Component, OutputPort, DataContract, Column, ...) and the component discriminator
    catalog_asset.py             # Internal, technology-agnostic asset descriptors produced by resolvers (AssetIdentity, CatalogAssetDescriptor, CatalogColumnDescriptor)
    constants.py                 # Supported technologies, required descriptor fields, Custom Metadata attribute names
  services/
    provision_service.py         # Orchestrates the full seven-phase provisioning flow
    unprovision_service.py       # Orchestrates unprovisioning (archive/purge)
    validation_service.py        # Descriptor-only checks for `/v1/validate` (no Atlan API calls)
    asset_service.py             # Technical asset (Table/View, Schema, Database) and hierarchy upsert against Atlan
    data_product_service.py      # DataProduct upsert, slug/display-name computation, DataDomain and owner resolution
    metadata_service.py          # Ensures the "Witboost" Custom Metadata typedef exists
    custom_metadata_service.py   # Applies Custom Metadata values to assets
    glossary_service.py          # Glossary/Business Term resolution and idempotent upsert
    term_association_service.py # Associates Business Terms to assets/columns
    entity_reference_service.py  # Looks up the Atlan entity for `/v1/entity/reference`
  resolvers/
    base.py                      # OutputPortResolver interface, ResolverContext, resolver error types
    registry.py                  # OutputPortResolverRegistry: technology -> resolver + Connection resolution
    databricks.py                # DatabricksOutputPortResolver
  settings/
    atlan_settings.py            # AtlanSettings via pydantic-settings
  utility/
    parsing_pydantic_models.py   # yaml.safe_load() + Pydantic model parsing helpers
```

- `main.py` hosts the FastAPI routes `POST /v1/validate`, `POST /v1/provision`, `GET /v1/provision/{token}/status`, `POST /v1/unprovision`, `POST /v1/updateacl`, `GET /v1/entity/reference`, plus the scaffold-only `POST /v2/validate` and `GET /v2/validate/{token}/status`. There is no dedicated health-check route: the sample Helm deployment points its readiness/liveness probes at FastAPI's built-in `/docs` page instead (see `helm/values-filled.yaml`).
- `GET /v1/provision/{token}/status`, `POST /v2/validate`, and `GET /v2/validate/{token}/status` are unused scaffold endpoints left over from the Tech Adapter template: they always return `SystemErr(error="Response not yet implemented")`. They exist to match the standard Tech Adapter API surface but this plugin never needs them, since `/v1/provision` and `/v1/unprovision` complete synchronously (see below).
- `POST /v1/updateacl` always returns `ProvisioningStatus(status=COMPLETED)` with an explanatory message: this plugin does not manage access to the underlying data platform, only Atlan cataloging, so there is nothing for it to reconcile.
- `dependencies.py` provides the FastAPI dependencies that parse and validate each incoming request's descriptor; `clients/atlan_client_factory.py` provides the cached `AtlanSettings`/`AtlanClient` dependencies.
- `services/provision_service.py` executes the full seven-phase provisioning flow synchronously in-request.
- `resolvers/` encapsulates technology-specific Output Port resolution behind a common interface.
- `models/` separates API schemas (`api_models.py`) from descriptor schemas (`data_product_descriptor.py`) and internal Atlan mapping objects (`catalog_asset.py`).

### Toolchain

| Tool | Role |
| --- | --- |
| Python ~3.11 | Service runtime |
| FastAPI (`fastapi[all]`) | HTTP framework, dependency injection, OpenAPI generation |
| Pydantic v2 + pydantic-settings | Request/response models, descriptor parsing, configuration loading |
| pyatlan SDK | All Atlan API interactions |
| tenacity | Retry with exponential backoff (used only in `term_association_service.py`, see [Phase 7](#phase-7-associate-business-terms)) |
| loguru | Structured logging with request correlation UUIDs |
| uvicorn | ASGI server |
| Poetry | Dependency management and packaging |
| ruff + mypy | Linting and static type checking |
| pytest + pytest-cov | Automated testing and coverage |

## Descriptor Model

The API accepts a `ProvisioningRequest` whose `descriptor` field contains the complete Data Product descriptor as a YAML string. The expected `descriptorKind` depends on the operation: `POST /v1/validate` expects `DATAPRODUCT_DESCRIPTOR` (the plain descriptor, before any provisioning happened), while `POST /v1/provision` and `POST /v1/unprovision` expect `DATAPRODUCT_DESCRIPTOR_WITH_RESULTS` (see §Provisioning below). Either way, `descriptor` carries the `DataProduct` directly at the top level — never wrapped in a `dataProduct`/`componentIdToProvision` envelope, since this plugin always reconciles the whole descriptor rather than a single targeted component.

Descriptor parsing is implemented with Pydantic v2 models in `models/data_product_descriptor.py`:

- `DataProduct` — root aggregate for the full descriptor
- `Component` — discriminated base model for descriptor components
- `OutputPort` — component subtype used by the catalog plugin
- `DataContract` — wraps the schema attached to an Output Port. It accepts both
  the legacy column-list shape (`schema[].dataType`) and the ODCS shape
  (`schema[].properties[].physicalType`); ODCS properties are normalized to the
  same internal column model before provisioning.
- `Column` — typed column definition used for Atlan Column reconciliation

For ODCS properties, `physicalType` is mapped to the Atlan column `dataType`
(with `logicalType` used only as a fallback when `physicalType` is absent).
Parameterized physical types (`VARCHAR(n)`, `CHAR(n)`, `DECIMAL(p,s)`,
`NUMERIC(p,s)`, `ARRAY<...>`) are decomposed into `dataType` plus
`dataLength`/`precision`/`scale`/`arrayDataType` so `dataType` alone stays a bare
OpenMetadata-supported type; `OpenMetadataColumn.databricks_data_type()`
reconstructs the full parameterized type from those fields, and the Databricks
resolver uses it (not the bare `dataType`) when building the Atlan `Column`, so
no length/precision/scale information is lost end-to-end. String tags are
normalized to the existing `tagFQN` model so Business Term association remains
compatible with legacy descriptors. ODCS quality checks are accepted as
descriptor fields but are not cataloged by this plugin.

The component list is discriminated by `kind` through a `parse_component` pattern:

```python
def parse_component(component_dict: dict) -> Component:
    kind = component_dict.get("kind")
    if kind == "outputport":
        return OutputPort.model_validate(component_dict)
    return Component.model_validate(component_dict)
```

`Component`, `OutputPort`, and `Workload` are configured with `extra="allow"`, so unrecognized fields on the descriptor are preserved rather than rejected; `DataProduct` uses Pydantic's default `extra="ignore"`, so unrecognized top-level fields are silently dropped. This keeps descriptor validation strict on the fields this plugin actually relies on, while tolerating fields owned by other parts of the platform (e.g. `taxonomy`, `projectOwner`) or added by upstream Tech Adapters (see `info`/`aclInfo`/`latestProvisioningOperation` in §Provisioning).

## Provisioning

`POST /v1/provision` and `POST /v1/unprovision` receive the Data Product descriptor after every other Tech Adapter has provisioned, enriched with their results (`descriptorKind: DATAPRODUCT_DESCRIPTOR_WITH_RESULTS`): both the Data Product and each component carry an additional `info` field (`publicInfo`/`privateInfo`), plus `aclInfo` and `latestProvisioningOperation`. This plugin does not use that enrichment — it only reads the descriptor fields needed to catalog Output Ports in Atlan — but must accept the enriched `descriptorKind` and tolerate the extra fields (see §Descriptor Model above). All interactions with Atlan use the pyatlan SDK, authenticated via an OAuth 2.0 Client Credentials Grant scoped to an Atlan Persona. `POST /v1/provision` and `POST /v1/unprovision` execute synchronously: all phases run within the request and a successful response returns HTTP 200 with `ProvisioningStatus(status=COMPLETED, ...)`.

### Phase 0: Descriptor Parsing

```text
1. Read body.descriptor from ProvisioningRequest
2. Execute yaml.safe_load(body.descriptor) -> descriptor_dict
3. Validate body.descriptorKind == DATAPRODUCT_DESCRIPTOR_WITH_RESULTS
4. Execute DataProduct.model_validate(descriptor_dict) -> DataProduct Pydantic model
5. Parse each component via the kind discriminator (parse_component pattern)
6. Extract Data Product metadata and OutputPort components from the validated model
7. On a descriptorKind mismatch, YAML parse failure, or Pydantic validation failure ->
   ValidationError(errors=["Unable to parse the descriptor.", "<exception message>"])
   (or, for a descriptorKind mismatch, a message naming the expected/actual kind).
   There is no dedicated failure code for these cases (see §Validation).
```

### Phase 1: Output Port Resolution

For each Output Port in the descriptor:

```text
1. Select resolver from OutputPortResolverRegistry based on technology field
2. On no matching resolver -> skip this Output Port (log at INFO level) and continue
   with the next one; do NOT fail the whole provisioning request. The Data Catalog
   Plugin is additive with respect to the deploy and must not catalog what it does
   not yet support, without blocking components it does support (see docs/validate
   §Validation for the equivalent validate-time behavior)
3. Resolve Connection qualifiedName from configuration (technology + environment)
4. Resolver computes:
   - Atlan asset typeName (Table, View, etc.)
   - Atlan qualifiedName (connection + platform hierarchy)
   - Column list from dataContract.schema
5. On Connection not found in config -> SystemErr(error="CONNECTION_NOT_CONFIGURED: ...")
6. On identity resolution failure -> SystemErr(error="IDENTITY_RESOLUTION_FAILED: ...")
```

### Phase 2: Prerequisite Metadata Definitions

Runs before Phase 3/4 create any carrier asset, since both the Custom Metadata set and the Business Terms referenced by the descriptor must exist before the assets that will carry them.

```text
1. Ensure the "Witboost" Custom Metadata type exists in Atlan
   - If not found → create via CustomMetadataDef.create("Witboost") with all 9
     attributes (see Configuration → Custom Metadata for the full attribute list)
   - Resolve each attribute's generated 22-character id by reading the typedef back
     and matching on display name; cache the {display_name: attribute_id} mapping —
     never hardcode ids, they are tenant-specific
   - On Atlan API error -> SystemErr(error="CUSTOM_METADATA_DEF_FAILED")
2. Extract every tagFQN referenced in the descriptor via `tags.tagFQN` and
   `dataContract.schema.tags.tagFQN` (data product root, any component root, and
   per column)
3. For each tagFQN, ensure the corresponding Business Term exists in the configured
   glossary:
   a. Search by qualifiedName in the configured glossary
   b. If found → reuse (do not overwrite description)
   c. If not found → create in the configured glossary
   d. On glossary not found -> SystemErr(error="GLOSSARY_NOT_FOUND")
4. Do NOT associate terms to assets/columns yet — assets do not exist until Phase 4;
   association happens in Phase 7
```

### Phase 3: Parent Hierarchy Upsert

Uses the output port's Unity Catalog coordinates (`specific.catalogNameOP` /
`specific.schemaNameOP`).

```text
1. Ensure Connection exists in Atlan (search by qualifiedName from config)
2. Ensure Database entity exists under the Connection
   - qualifiedName: <connectionQN>/<catalogNameOP>
3. Ensure Schema entity exists under the Database
   - qualifiedName: <databaseQN>/<schemaNameOP>
4. Upsert both via pyatlan save()
5. On Connection not found in Atlan -> SystemErr(error="CONNECTION_NOT_FOUND")
6. On any other Atlan API error while upserting the Database/Schema -> SystemErr(error="ASSET_UPSERT_FAILED")
   (the same error code used by Phase 4, since both share the same upsert helper)
```

### Phase 4: Technical Asset Upsert

```text
1. Search for existing asset by typeName + qualifiedName
2. If found → update metadata
3. If not found → create new asset under the Schema
4. Reconcile columns:
   - Add new columns from the descriptor
   - Update changed columns (dataType, description)
   - Archive columns present in Atlan but absent from the descriptor
5. Collect the Atlan GUID of each created/updated asset (from save() response)
6. Best-effort: if the Output Port carries `info.publicInfo.tableUrl.href` (only
   present when the descriptor is `DATAPRODUCT_DESCRIPTOR_WITH_RESULTS`, i.e. on
   `/v1/provision`/`/v1/unprovision`, never on `/v1/validate`), attach it as a
   native Atlan `Link` asset named "Open in Databricks" on the Table/View, so the
   asset's "Links" tab has a one-click deep link back to the source table/view in
   its source technology. The Link's qualifiedName is derived deterministically as
   `<assetQualifiedName>/Open in Databricks`, so re-provisioning updates the same
   Link in place instead of creating duplicates. Any Atlan API failure here is
   logged and swallowed — it is a convenience, not part of the Failure Matrix, so
   it must never fail provisioning.
7. On Atlan API error -> SystemErr(error="ASSET_UPSERT_FAILED")
```

### Phase 5: Apply Custom Metadata

Applies the typedef ensured in Phase 2 to the assets created in Phase 4. Applicable to
Table, View, and Column (DataProduct is handled in Phase 6, since it is not created
until then).

```text
1. For each managed technical asset (Table/View) and its columns, set the applicable
   attributes using the attribute ids resolved in Phase 2 (never display names):
   - Domain, Environment, Output Port URN (= Witboost Component ID), Witboost Link,
     Last Synced At
2. Read back at least one attribute value after writing, to confirm the write applied
   (Atlan does not error on a misdirected/display-name write — see Configuration →
   Custom Metadata)
3. On Atlan API error -> SystemErr(error="CUSTOM_METADATA_FAILED")
```

**Why the Output Port only carries "Domain" as Custom Metadata, never as a native
Atlan Domain relationship**: Atlan's Domain mesh feature only supports `DataDomain`
and `DataProduct` as domain-scoped types (`pyatlan.model.typedef._all_domain_types`);
a Table/View/Column cannot natively belong to a Domain in Atlan's object model —
there is no `domain_qualified_name`-equivalent relationship field on those asset
types. The Witboost domain therefore reaches the Output Port only as free-text via
this Custom Metadata attribute; its native Domain assignment is exclusively on the
DataProduct (see Phase 6).

### Phase 6: DataProduct Upsert & Linking

```text
1. Resolve DataDomain qualifiedName from domain mapping configuration
   - DataDomain qualifiedName = domain name (e.g., "finance")
2. Build the asset selection DSL via pyatlan SDK query-builder constructs (nested
   bool query) that matches the managed assets — never hand-craft the DSL JSON, and
   always rebuild it from scratch on every push, even if the DataProduct already
   exists, because manual UI edits (Edit-assets modal) can freeze a previously
   SDK-authored DSL into a static GUID list
   - Query by qualifiedName of all Output Port assets resolved in Phase 1
3. Create or update DataProduct via pyatlan:
   - Compute slug: {normalized_dp_name}-{environment}-v{major_version}
     (e.g., "risk-finance-production-v0" from ID urn:dmb:dp:finance:risk-finance:0 + env production)
   - Compute display name: {data_product.name}-v{major_version} (e.g., "Risk Finance-v0") — the
     major-version suffix lets different major versions of the same Data Product coexist as
     distinct Atlan DataProducts instead of colliding on `name` (see below)
   - Look up an existing DataProduct by the computed qualifiedName first; if not found,
     fall back to a by-name search (using the versioned display name) scoped to the domain
     (including archived assets) — Atlan does not honor the client-requested `qualifiedName`
     when creating a DataProduct (it assigns its own server-generated identifier instead), so
     this by-name fallback is the *normal* way an existing DataProduct is found again on every
     subsequent provisioning call, not just an edge case
   - Any non-DELETED match is reused regardless of whether it was found by qualifiedName or by
     name: since the display name already embeds the major version, a by-name match is
     guaranteed to be the same major version of this same Data Product (Atlan's own
     domain-scoped name uniqueness rules out any other unrelated, still-active DataProduct
     sharing this exact name)
   - If the match is ARCHIVED: restore it before reusing it (keeps re-provisioning after an
     unprovision idempotent)
   - If new: DataProduct.creator(name=slug, domain_qualified_name=domain_qn, asset_selection=search_request)
   - After creator: set name = versioned display name (e.g., "Risk Finance-v0") before save()
   - If existing: DataProduct.updater(qualified_name=existing_qn, name=versioned_display_name)
   - DataProduct qualifiedName = "{domain_qn}/product/{slug}" (e.g., "finance/product/risk-finance-production-v0")
4. Set daap_output_port_guids with the GUIDs collected in Phase 4 — the DSL built in
   step 2 MUST match at least this same set of GUIDs; an output port GUID present
   here but not matched by the DSL will silently fail to render, with no error from
   Atlan
5. Never set daap_input_port_guids — Atlan derives it automatically from other Data
   Products' output ports
6. Apply Custom Metadata to the DataProduct: Data Product URN, Data Product Version,
   Domain, Environment, Data Product Owner, Dev Group, Witboost Link, Last Synced At
   (by attribute id, resolved in Phase 2)
7. Reconcile ownership on EVERY upsert (create AND update, so an owner change
   propagates on re-provisioning, not just at first creation) from
   `dataProduct.dataProductOwner` ONLY (the separate `ownerGroup` descriptor field
   is NOT used for Atlan ownership). Atlan's `owner_users`/`owner_groups` require
   its own internal `username`/group `name`, NOT an email address or display
   alias, so the parsed identity must be resolved against the Atlan user/group
   directory before being written:
   - `user:<local>_<domain>` -> build an email as `<local>@<domain>` (strip
     prefix, replace the LAST underscore with `@`), then look it up via
     `client.user.get_by_emails(emails=[email])` (exact list match) and
     re-check the returned records' `email` for an exact match. Exactly one
     match -> owner_users={"<match.username>"}; zero or multiple matches (or
     any Atlan API failure) -> owner_users left unset, warning logged
   - `group:<group>` -> the alias is looked up via
     `client.group.get_by_name(alias=<group>)` (a contains/`ilike` search) and
     re-checked against the returned records' `alias` for an exact match.
     Exactly one match -> owner_groups={"<match.name>"} (Atlan's internal group
     name, not the display alias); zero or multiple matches (or any Atlan API
     failure) -> owner_groups left unset, warning logged
   - missing value -> clear both (None), no lookup performed
   - unrecognized prefix -> treated as a user email as-is (same lookup flow as
     the `user:` case), with a warning logged
   - Provisioning is never aborted by a failed/ambiguous owner lookup — the
     DataProduct is still upserted with ownership left unset
8. Set daap_criticality / daap_sensitivity from the optional descriptor fields
   `criticality` / `sensitivity` (Atlan's native DataProduct "Criticality"/
   "Sensitivity" dropdowns, backed by pyatlan's `DataProduct` asset model
   and `DataProductCriticality`/`DataProductSensitivity` enums: Low/Medium/High
   and Public/Internal/Confidential respectively). Left unset when the
   descriptor omits them.
9. save() the DataProduct
10. On DataDomain not found -> SystemErr(error="DOMAIN_NOT_FOUND")
11. On Atlan API error -> SystemErr(error="DATAPRODUCT_UPSERT_FAILED")
```

### Phase 7: Associate Business Terms

Associates the terms ensured in Phase 2 to the assets created in Phase 4 and the
DataProduct created in Phase 6. Term references come from the Witboost
**Business Concept Map** mechanism (see
[How to Integrate with the Business Concept Map](https://docs.witboost.com/docs/data-teams/market-plane/how-to-guides/how-to-integrate-with-business-concept-map#step-by-step)).

```text
1. For each tagFQN extracted in Phase 2, resolve the corresponding Business Term
2. Associate terms to assets and/or columns via Atlan assignedTerms
   - Root-level / component-level tagFQN -> associate to the technical asset (Table/View)
   - dataContract.schema-level tagFQN -> associate to the specific Column
3. On association failure -> SystemErr(error="TERM_ASSOCIATION_FAILED")
```

---

## Unprovisioning

```text
For each Output Port in the descriptor:
  1. Resolve the asset's qualifiedName (same logic as provisioning Phase 1)
  2. Search for asset by typeName + qualifiedName
  3. If found: archive or delete asset + columns (based on unprovision strategy config)
  4. If not found → skip (idempotent)

After all Output Ports:
  5. Since every Output Port in the descriptor is (re-)processed on every call
     (this plugin re-derives the complete catalog state from the complete
     descriptor, it never accumulates partial state across scoped calls),
     unprovisioning always tears down the entire DataProduct: archive or delete
     it (based on unprovision strategy config), skipping only if it was never
     created (e.g. every Output Port had an unsupported technology)
  6. Never delete: Connection, DataDomain, Business Terms
```

---

## Validation

The `validate` operation (`POST /v1/validate`) performs descriptor-only checks before any Atlan API call. It expects `descriptorKind: DATAPRODUCT_DESCRIPTOR` (not the `DATAPRODUCT_DESCRIPTOR_WITH_RESULTS` enriched form accepted by `provision`/`unprovision`, see §Provisioning), since validation happens before any Tech Adapter — including this one — has provisioned anything.

Output Ports whose `technology` has no registered resolver (see `SUPPORTED_TECHNOLOGIES`) are **skipped silently**: the Data Catalog Plugin is additive with respect to the deploy and must not fail validation for components it simply does not catalog yet. They are neither validated further (no metadata/schema checks) nor reported as an error.

Descriptor parsing itself (invalid YAML, or a shape that does not fit the `DataProduct` Pydantic model) happens before validation runs, in `dependencies.py`, and is reported as a plain `ValidationError(errors=["Unable to parse the descriptor.", "<exception message>"])` — there is no dedicated failure code for it, unlike the checks below.

| Check | Field | Failure Code |
| --- | --- | --- |
| DP name present | `name` | `VALIDATION_MISSING_DP_NAME` |
| Domain present | `domain` | `VALIDATION_MISSING_DOMAIN` |
| At least one Output Port | components with `kind == outputport` | `VALIDATION_NO_OUTPUT_PORTS` |
| Technology-specific table metadata present | E.g. `specific.catalogNameOP`, `schemaNameOP`, `viewNameOP` on each supported (Databricks) OP — the OP's own Unity Catalog coordinates used for Atlan identity resolution | `VALIDATION_INCOMPLETE_METADATA` |
| Schema present | `dataContract.schema` non-empty on each supported OP | `VALIDATION_MISSING_SCHEMA` |

---

## Entity Reference

The `entity reference` operation (`GET /v1/entity/reference?componentId=...`) is synchronous and does not receive a descriptor — only the Output Port's `componentId` (URN). Because of that, it cannot recompute the Atlan `qualifiedName` the way `provision`/`unprovision` do (see [Entity Identity and qualifiedName](#entity-identity-and-qualifiedname)); instead it looks the asset up by the `Output Port URN` Custom Metadata attribute, which `provision` already writes onto every Table/View (see [Phase 5: Apply Custom Metadata](#phase-5-apply-custom-metadata)):

```text
1. Search Table/View assets whose `Output Port URN` Custom Metadata value equals `componentId`
2. If exactly one match: return its Atlan link ("<base_url>/assets/<guid>/overview") as `EntityReference.reference`
3. If more than one match (not expected — Output Port URNs are unique per component):
   log a warning and use the first match
4. If no match: return a 500 SystemErr (`ENTITY_NOT_FOUND`) — the official schema exposes only 200/500
   for this endpoint, so there is no dedicated "not found" status code
5. If the Atlan search itself fails: return a 500 SystemErr (`ENTITY_REFERENCE_LOOKUP_FAILED`)
```

---

## Output Port Resolver Design

### Resolver Interface

```python
class OutputPortResolver:
    def supports(self, output_port: OutputPort) -> bool:
        """Returns True if this resolver handles the given OP's technology."""
        ...

    def resolve(self, output_port: OutputPort, context: ResolverContext) -> CatalogAssetDescriptor:
        """Resolves the OP to an Atlan asset identity + column list."""
        ...

class ResolverContext:
    connection_qualified_name: str   # target Atlan Connection QN (from config)
    environment: str
    asset_mapping_config: dict = {}  # reserved for future per-technology type-mapping rules; unused today
```

### Databricks Resolver

**Supports**: `technology == "databricks"` (case-insensitive)

**Type resolution**: every Databricks Output Port is currently mapped to an Atlan `View` asset (`type_name="View"`, hardcoded). `ResolverContext.asset_mapping_config` exists on the interface for a future configurable Table-vs-View mapping but is not read by this resolver today.

**Identity resolution**: Constructs `qualifiedName` from the Output Port's own Unity Catalog coordinates (never the upstream source-table coordinates in `specific.catalogName`/`schemaName`/`tableName`, which are lineage-only inputs):

```text
<connectionQN>/<specific.catalogNameOP>/<specific.schemaNameOP>/<specific.viewNameOP>
```

Example: `default/databricks/1692345678/risk-finance/orders-output-port/orders_view`

### Adding a New Technology

To support a new technology (e.g., BigQuery):

1. Implement `OutputPortResolver` (e.g., `BigQueryOutputPortResolver`)
2. Register it in the resolver registry
3. Add Connection mapping for the new technology in configuration (see [`connection_mapping`](#connection_mapping))
4. **No changes to the Provisioning Service, mappers, or Atlan service layer**

---

## Domain Model

The plugin uses an internal generic model that is **technology-agnostic** for the Output Port's target asset. Resolvers produce these objects (`models/catalog_asset.py`); the asset upsert layer consumes them.

```yaml
CatalogAssetDescriptor
  connection_identity: AssetIdentity      # target Connection
  asset_identity: AssetIdentity           # typeName ("View") + qualifiedName
  schema_identity: AssetIdentity          # parent Schema
  database_identity: AssetIdentity        # parent Database
  name: str
  description: str | None
  columns: list[CatalogColumnDescriptor]

AssetIdentity
  type_name: str           # "Table", "View", "Schema", "Database", "Connection"
  qualified_name: str      # hierarchical, deterministic

CatalogColumnDescriptor
  name: str
  data_type: str
  description: str | None
```

The DataProduct itself (Phase 6) is not represented by a dedicated domain model: `upsert_data_product()` (`services/data_product_service.py`) works directly off the parsed descriptor's `DataProduct` object plus the Output Port GUIDs/qualifiedNames resolved earlier in the flow.

---

## Entity Identity and qualifiedName

All asset identity in Atlan is based on `typeName` + `qualifiedName`. The plugin **never stores or relies on Atlan GUIDs**.

| Entity | qualifiedName pattern | Source |
| --- | --- | --- |
| Connection | `default/<connectorType>/<epoch>` | Pre-provisioned |
| DataDomain | `<domainName>` | Pre-provisioned |
| Database | `<connectionQN>/<catalogName>` | Plugin (upsert) |
| Schema | `<databaseQN>/<schemaName>` | Plugin (upsert) |
| Table / View | `<schemaQN>/<objectName>` | Plugin (upsert or reuse) |
| Column | `<tableQN>/<columnName>` | Plugin (upsert) |
| DataProduct | `<domainQN>/product/<normalizedName>-<env>-v<major>` | Plugin (upsert via `DataProduct.creator()`) |
| GlossaryTerm | `<glossaryQN>/<termName>` | Plugin (upsert or reuse) |

**DataDomain qualifiedName**: For top-level domains, the `qualifiedName` equals the domain name (e.g., `finance`). This is the format generated by the pyatlan `DataDomain.creator()` method.

**DataProduct qualifiedName**: The plugin constructs a slug from the Witboost Data Product ID, environment, and major version: `{normalized_name}-{environment}-v{major_version}`. This slug is passed as `name` to `DataProduct.creator()`, producing a `qualifiedName` of `{domain_qn}/product/{slug}` (e.g., `finance/product/risk-finance-production-v0`). The display name is then overwritten to `{data_product.name}-v{major_version}` (e.g., `Risk Finance-v0`, see `compute_display_name()`) before saving. This isolates environments and minor/patch version updates on a shared Atlan tenant (same major version -> same `qualifiedName` -> update in place).

**Major version coexistence**: Atlan enforces domain-scoped uniqueness on a DataProduct's `name` independently of its `qualifiedName`. Different major versions of the same Data Product already get a different `qualifiedName` (via `compute_slug()`); the `-v{major_version}` suffix on the display name (`compute_display_name()`) additionally keeps them from colliding on `name`, so multiple major versions can be provisioned and remain active side by side (e.g. `Risk Finance-v0` and `Risk Finance-v1` as two distinct DataProducts). Atlan does not honor the client-requested `qualifiedName` when creating a DataProduct — it assigns its own server-generated identifier instead — so on every subsequent provisioning call the direct qualifiedName lookup misses and the by-name fallback is what actually finds the existing DataProduct; this is expected and it is always reused (restored first if archived), since a match found by the versioned display name is guaranteed to be the same major version of this same Data Product.

**Connection qualifiedName**: Generated by Atlan as `default/{connector_type}/{epoch}` when the Connection is created. The epoch is unique per Connection. The plugin references pre-provisioned Connections by their actual `qualifiedName` from configuration.

---

## Entity Mapping Reference

| Witboost Concept | Atlan Technical Layer | Atlan Business Layer | Managed By |
| --- | --- | --- | --- |
| Domain | — | DataDomain (QN = domain name) | Pre-provisioned |
| Data Product | — | DataProduct (QN = `{domain_qn}/product/{slug}`) | Plugin |
| Output Port | Table / View (resolved) | Linked via `daapOutputPortGuids` + asset selection DSL | Plugin |
| Column | Column | — | Plugin |
| Business Term | — | GlossaryTerm | Plugin (upsert/reuse) |
| Connection | Connection (QN = `default/{type}/{epoch}`) | — | Pre-provisioned |
| Witboost traceability | Custom Metadata (`Witboost`, 9 attributes) on DataProduct, Table, View, Column | — | Plugin (create type + apply) |

> **Invariant**: the asset selection DSL and `daapOutputPortGuids` are two
> independent mechanisms that must always agree — an Output Port GUID present in
> `daapOutputPortGuids` but not matched by the DSL query silently fails to render,
> with no error from Atlan. The DSL must be rebuilt from scratch via the pyatlan
> SDK query-builder on every provision call, never hand-crafted or reused as-is,
> because a manual Atlan UI edit (Edit-assets modal) can freeze it into a static
> GUID list. `daapInputPortGuids` must never be set by the plugin — Atlan derives
> it automatically. See [Phase 6: DataProduct Upsert & Linking](#phase-6-dataproduct-upsert--linking).

---

## Business Terms

### Glossary Hierarchy in Atlan

```text
Business Graph (glossary)
  └── Category (optional)
        └── GlossaryTerm
```

### Term Identity

Terms are identified by `qualifiedName` within their glossary, not by display name. The plugin searches by `qualifiedName` to ensure uniqueness.

### Term Source

Term references are `tagFQN` values found under `tags[].tagFQN` in the descriptor
(data product root, component root, or per column via
`dataContract.schema[].tags[].tagFQN` — see
[Phase 2: Prerequisite Metadata Definitions](#phase-2-prerequisite-metadata-definitions)
for term creation and [Phase 7: Associate Business Terms](#phase-7-associate-business-terms)
for association). These paths are hardcoded by the plugin and do not depend on Witboost's
platform-wide business-concepts configuration. The `tagFQN` value becomes the term's
display name / identity input (e.g. `Compensation`, `PII`) and is resolved to a
`qualifiedName` within the configured glossary.

### Idempotent Upsert

1. Search for term by `qualifiedName` in the target glossary
2. If found → **reuse** without overwriting description
3. If not found → **create** the term

Terms are **never deleted** during unprovisioning — they may be shared across Data Products.

### Associations

| Association | Mechanism |
| --- | --- |
| Term → Asset | Atlan `assignedTerms` relationship on the asset |
| Term → Column | Atlan `assignedTerms` relationship on the column |

`client.append_terms` resolves its target asset by GUID through a search-index query (Elasticsearch-backed), which is only eventually consistent: an asset created or updated earlier in the same provisioning request — most notably the DataProduct itself, just upserted in Phase 6 — can briefly return `ATLAN-PYTHON-404-001` ("Asset with GUID ... does not exist") even though the save already succeeded. `associate_business_terms()` (`term_association_service.py`) retries with exponential backoff on this specific `NotFoundError` (up to 10 attempts) to ride out that delay before giving up and raising `TERM_ASSOCIATION_FAILED`.

---

## Atlan Client Initialization

`clients/atlan_client_factory.py` builds the process-wide `pyatlan` `AtlanClient` used by every service. There is no custom wrapper class around it: services call the `pyatlan` SDK directly (`client.save(...)`, `client.asset.find_...`, `client.custom_metadata_cache`, etc.).

```python
def build_atlan_client(settings: AtlanSettings) -> AtlanClient:
    return AtlanClient(
        base_url=settings.base_url,
        oauth_client_id=settings.oauth_client_id,
        oauth_client_secret=settings.oauth_client_secret,
    )
```

`get_atlan_settings()` and `get_atlan_client()` are cached (`functools.lru_cache`) so `AtlanSettings` is parsed and the `AtlanClient` is constructed only once per process; both are exposed as FastAPI dependencies used by the routes in `main.py`.

Authentication uses an OAuth 2.0 Client Credentials Grant: the pyatlan SDK owns token exchange, caching, and 401-triggered refresh internally once `oauth_client_id`/`oauth_client_secret` are provided. Credentials are never logged.

Responsibilities that might be expected from a dedicated "Atlan client service" — retrying transient Atlan failures, rate-limit handling, or a single upsert/search/archive/purge API — are **not** centralized in one place today:

- Asset upsert/search/archive/purge is implemented directly in `services/asset_service.py` and `services/data_product_service.py` against the pyatlan SDK.
- The Custom Metadata typedef (creation, attribute id resolution and caching) is implemented in `services/metadata_service.py`.
- The only retry logic in the codebase is the fixed retry described in [Phase 7: Associate Business Terms](#phase-7-associate-business-terms) (`services/term_association_service.py`); it does not apply to any other Atlan API call, and the `retry` configuration key (see below) is currently unused.

---

## Configuration

The plugin is configured via a YAML file mapped into `AtlanSettings` (`settings/atlan_settings.py`) through Pydantic Settings. All sections are described below with their purpose and expected values.

```python
class AtlanSettings(BaseSettings):
    base_url: str
    oauth_client_id: str
    oauth_client_secret: str
    connection_mapping: dict[str, dict[str, str]] = {}
    domain_mapping: dict[str, str] = {}
    custom_metadata: dict
    glossary: dict
    unprovision: dict
    retry: dict

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ATLAN_",
        extra="ignore",
    )
```

Every field can be set two ways, environment variable taking precedence over the file:

- An environment variable named `ATLAN_<FIELD_NAME>` (e.g. `ATLAN_BASE_URL`), which always wins.
- A key in a YAML file, read from `config/application.yaml` (relative to the working directory the service is started from) by default, overridable via the `ATLAN_CONFIG_FILE` environment variable. A missing file is not an error — every field then falls through to the environment variable or its default.

`oauth_client_id` and `oauth_client_secret` must always be provided as `ATLAN_OAUTH_CLIENT_ID` and `ATLAN_OAUTH_CLIENT_SECRET` environment variables (for example via `.env` for local development, or a Kubernetes Secret in a deployment — see below) — never in the YAML file or the descriptor. Authentication uses an OAuth 2.0 Client Credentials Grant; see [Atlan Client Initialization](#atlan-client-initialization) for the token exchange/refresh mechanism.

For local development, copy `tech-adapter/config/application-sample.yaml` to `tech-adapter/config/application.yaml` (the latter is git-ignored) and adjust it for your tenant.

For a Helm/Kubernetes deployment (see `helm/`):

- The non-secret fields default to `helm/files/application.yaml`, packaged into a ConfigMap by `helm/templates/configmap.yaml` and mounted into the container at `config/application.yaml` (`ATLAN_CONFIG_FILE` is set accordingly by `helm/templates/deployment.yaml`). Setting `values.configOverride` to a non-empty YAML string replaces this file's content entirely for that deployment (it is not merged with the default).
- `oauth_client_id`/`oauth_client_secret` are provided by a pre-existing Kubernetes Secret named in `values.secretName`, injected into the container via `envFrom.secretRef` as `ATLAN_OAUTH_CLIENT_ID`/`ATLAN_OAUTH_CLIENT_SECRET`. This chart never creates that Secret itself — see `helm/sample/` for an example based on the External Secrets Operator's `fake` provider, meant for local/dev smoke testing only.

```yaml
base_url: https://tenant.atlan.com
```

### `connection_mapping`

Maps each supported technology and environment to the `qualifiedName` of the corresponding pre-provisioned Atlan Connection. The plugin uses this mapping during Output Port resolution (Phase 1) to determine under which Connection the technical assets should be created. Both the technology and the environment keys are matched case-insensitively against the descriptor's `technology`/`environment` values.

Each Connection must already exist in Atlan. Its `qualifiedName` has the format `default/{connectorType}/{epoch}` and can be retrieved from the Atlan UI or via the pyatlan SDK.

```yaml
connection_mapping:
  databricks:
    production: "default/databricks/1692345678"
    development: "default/databricks/1692345999"
  # Future technologies:
  # snowflake:
  #   production: "default/snowflake/1692346000"
```

### `domain_mapping`

Maps each Witboost domain name to the corresponding Atlan DataDomain. The plugin uses this mapping during DataProduct upsert (Phase 6) to link the DataProduct to the correct DataDomain.

Each mapped value may be either the DataDomain's `qualifiedName` (e.g. `default/domain/2CZLqmfGjKmzHBYX0MwMn/super`) or its human-readable display **name** as shown in the Atlan UI (e.g. `Agile Lab`) — resolution tries the value as a `qualifiedName` first, falling back to a by-name search if that misses. The by-name fallback is a convenience for operators who only know the UI name; if more than one DataDomain shares that name (Atlan does not enforce domain name uniqueness), provisioning fails with `DOMAIN_NOT_FOUND` asking for the unambiguous `qualifiedName` to be configured instead.

For top-level domains, the `qualifiedName` in Atlan equals the domain name (as generated by `DataDomain.creator()`). This mapping allows decoupling the Witboost domain name from the Atlan DataDomain name when they differ.

**Fallback behavior**: if a Witboost domain is not present in the mapping, the plugin uses the domain name from the descriptor as-is (e.g., `domain: finance` → looked up as either the DataDomain QN or name `finance`). This avoids requiring explicit configuration when the names already match.

```yaml
domain_mapping:
  agilefinance: "Agile Lab"    # Witboost "agilefinance" → Atlan DataDomain named "Agile Lab" (resolved by name)
  marketing: "default/domain/2CZLqmfGjKmzHBYX0MwMn/super"  # → resolved directly by qualifiedName
  # "sales" is not listed → the plugin will try "sales" as both a QN and a name
```

### `custom_metadata`

Controls the Atlan Custom Metadata type used for Witboost traceability. When `auto-create` is `true`, the plugin creates the type and its 9 attributes at startup if they do not exist (see [Phase 2: Prerequisite Metadata Definitions](#phase-2-prerequisite-metadata-definitions)), scoped as follows:

| Attribute | Applies to | Notes |
| --- | --- | --- |
| Data Product URN | DataProduct | Witboost Data Product ID (e.g., `urn:dmb:dp:...`), major version only |
| Data Product Version | DataProduct | Full version, kept separately since the URN only carries the major version |
| Domain | DataProduct, Table, View, Column | Witboost domain name |
| Environment | DataProduct, Table, View, Column | Deployment environment (e.g., `production`) |
| Output Port URN | Table, View, Column | Witboost Component ID (e.g., `urn:dmb:cmp:...`) |
| Data Product Owner | DataProduct | Witboost Data Product Owner |
| Dev Group | DataProduct | Witboost dev group |
| Witboost Link | DataProduct, Table, View, Column | Reserved for a deep link to the corresponding entity in the Witboost Marketplace. The attribute is defined and written on every provisioning call, but its value is always left unset today: no configuration key exists yet for the Witboost Marketplace base URL needed to build the link |
| Last Synced At | DataProduct, Table, View, Column | Timestamp of the last successful provisioning, for staleness tracking |

> **Implementation note**: Atlan addresses custom metadata attributes by their
> generated 22-character attribute id, never by display name — writing by display
> name is silently ignored, with no error. The plugin resolves
> `{display_name: attribute_id}` by reading the typedef back at runtime (never
> hardcoding ids, since they are tenant-specific) and caches the mapping for the
> lifetime of the process.

```yaml
custom_metadata:
  type-name: Witboost
  auto-create: true
```

### `glossary`

Controls how the plugin manages Business Terms in Atlan.

| Property | Values | Description |
| --- | --- | --- |
| `strategy` | `per-domain` / `shared` | `per-domain`: uses one glossary per Witboost domain. `shared`: uses a single glossary for all domains |
| `shared-glossary-name` | string | Name of the shared glossary (used only when `strategy == shared`) |
| `create-if-missing` | boolean | If `true`, the plugin creates the glossary when it does not exist. Set to `false` when the configured Atlan credentials do not have permission to create glossaries — provisioning then fails with `GLOSSARY_NOT_FOUND` instead of attempting creation |
| `glossary-mapping` | `dict[string, string]` | Only used when `strategy == per-domain`. Maps each Witboost domain to the name of an already-existing Atlan glossary, mirroring `domain_mapping`. Use this when glossaries cannot be auto-created and/or must not be named after the Witboost domain |
| `default-glossary-name` | string (optional) | Only used when `strategy == per-domain`. Fallback glossary name for any Witboost domain not listed in `glossary-mapping`. If omitted, unmapped domains fall back to using the Witboost domain name as-is (the pre-existing default behavior) |

```yaml
glossary:
  strategy: per-domain
  shared-glossary-name: Witboost
  create-if-missing: true
  glossary-mapping:
    agilefinance: "Finance Glossary"
  default-glossary-name: Witboost
```

### `unprovision`

Determines the behavior when an Output Port is unprovisioned.

| Strategy | Behavior |
| --- | --- |
| `archive` | Soft-delete: the asset is hidden from the UI but can be restored by an admin |
| `purge` | Hard-delete: the asset is permanently removed from Atlan |

```yaml
unprovision:
  strategy: archive
```

### `retry`

> **Note**: this configuration key is currently defined in `AtlanSettings` and
> accepted from the YAML file / environment, but is **not wired to any retry
> mechanism** in the codebase — changing these values today has no effect. The
> only retry logic implemented is a fixed, non-configurable retry (up to 10
> attempts, exponential backoff) around a specific eventually-consistent lookup
> during [Phase 7: Associate Business Terms](#phase-7-associate-business-terms)
> (`services/term_association_service.py`). This is a known gap between the
> documented intent and the current implementation.

```yaml
retry:
  max-attempts: 3
  backoff-multiplier: 2
  initial-delay-ms: 1000
```

### Authentication

`oauth_client_id`/`oauth_client_secret` (the OAuth 2.0 Client Credentials Grant credentials used for all Atlan SDK operations) must be stored securely via `ATLAN_OAUTH_CLIENT_ID`/`ATLAN_OAUTH_CLIENT_SECRET`, provided by a Kubernetes Secret in a deployment (see §Configuration above) or a local `.env` file for development.

Neither value is ever logged, even at DEBUG level, nor returned in provisioning results (`publicInfo` / `privateInfo`).

---

## Error Response Structure

The FastAPI routes return Pydantic response models declared per-route; `check_response()` (`check_return_type.py`) looks up the returned model's type in that route's declared `responses` and picks the matching HTTP status code. The two models actually used for errors are simple, and neither carries structured `moreInfo`/`solutions`/`inputErrorField` fields — those richer shapes exist as separate, currently-unused Pydantic models (`RequestValidationError`, `ErrorMoreInfo`) in `models/api_models.py`, but no route ever constructs them today.

| Returned model | HTTP status | Meaning |
| --- | --- | --- |
| `ProvisioningStatus(status=COMPLETED, result="", info=Info(...))` | 200 | Successful synchronous `provision` / `unprovision` |
| `ValidationResult(valid=True, ...)` | 200 | Successful synchronous `validate` |
| `EntityReference(reference=...)` | 200 | Successful `GET /v1/entity/reference` |
| `ValidationError(errors=[...])` | 400 | Descriptor parsing or descriptor-kind mismatch (`/v1/validate`, `/v1/provision`, `/v1/unprovision`, `/v1/updateacl`) |
| `SystemErr(error="<CODE>: <message>")` | 500 | Any phase failure during `provision`/`unprovision`, or a lookup failure on `/v1/entity/reference`. `<CODE>` is the failing operation's error `code` (see the Failure Matrix below); if the raised exception has no `code` attribute, `<CODE>` is `UNEXPECTED_ERROR` |

**Successful provisioning / unprovisioning** (`ProvisioningStatus`):

```json
{
  "status": "COMPLETED",
  "result": "",
  "info": {
    "publicInfo": {},
    "privateInfo": {}
  }
}
```

**Descriptor parsing failure (HTTP 400, `ValidationError`):**

```json
{
  "errors": [
    "Unable to parse the descriptor.",
    "1 validation error for DataProduct\nname\n  Field required [type=missing, ...]"
  ]
}
```

**Phase failure during provisioning (HTTP 500, `SystemErr`):**

```json
{
  "error": "DOMAIN_NOT_FOUND: No Atlan DataDomain found for 'finance' (qualifiedName or name lookup both missed)"
}
```

---

## Failure Matrix

| # | Phase / Area | Failure | Code | Action |
| --- | --- | --- | --- | --- |
| 1 | Parsing (Phase 0) | Descriptor kind mismatch, invalid YAML, or Pydantic validation failure | *(none — plain `ValidationError` message, HTTP 400)* | Fix the descriptor's `descriptorKind`/YAML/shape and resubmit |
| 2 | Validation (`/v1/validate` only) | Missing DP name | `VALIDATION_MISSING_DP_NAME` | Ensure `name` is present |
| 3 | Validation (`/v1/validate` only) | Missing domain | `VALIDATION_MISSING_DOMAIN` | Ensure `domain` is present |
| 4 | Validation (`/v1/validate` only) | No Output Ports | `VALIDATION_NO_OUTPUT_PORTS` | Add at least one `kind: outputport` component |
| 5 | Validation (`/v1/validate` only) | Missing technology-specific table metadata | `VALIDATION_INCOMPLETE_METADATA` | Fill in the required `specific.*` fields for the OP's technology |
| 6 | Validation (`/v1/validate` only) | Missing schema | `VALIDATION_MISSING_SCHEMA` | Add a non-empty `dataContract.schema` |
| 7 | Resolution (Phase 1) | No resolver for technology | *(none — the Output Port is skipped, not an error)* | Add a resolver for this technology, or fix the `technology` field if it was a typo |
| 8 | Resolution (Phase 1) | Connection not found in config | `CONNECTION_NOT_CONFIGURED` | Add a `connection_mapping` entry for this technology + environment |
| 9 | Resolution (Phase 1) | Cannot compute qualifiedName | `IDENTITY_RESOLUTION_FAILED` | Ensure the technology-specific `specific.*` fields are present and valid |
| 10 | Prerequisite Metadata (Phase 2) | Custom Metadata typedef cannot be ensured | `CUSTOM_METADATA_DEF_FAILED` | Check Atlan API permissions for typedef operations |
| 11 | Prerequisite Metadata (Phase 2) | Configured glossary not found | `GLOSSARY_NOT_FOUND` | Pre-provision the glossary or enable `create-if-missing` |
| 12 | Hierarchy (Phase 3) | Connection not found in Atlan | `CONNECTION_NOT_FOUND` | Pre-provision the Connection in Atlan |
| 13 | Hierarchy (Phase 3) / Asset (Phase 4) | Atlan API error on Database/Schema/Table/View upsert | `ASSET_UPSERT_FAILED` | Check Atlan API permissions; review the asset payload |
| 14 | Custom Metadata (Phase 5) | Atlan API error on Custom Metadata apply | `CUSTOM_METADATA_FAILED` | Check Atlan API permissions for Custom Metadata operations |
| 15 | DataProduct (Phase 6) | DataDomain not found in Atlan | `DOMAIN_NOT_FOUND` | Pre-provision the DataDomain in Atlan, or fix `domain_mapping` |
| 16 | DataProduct (Phase 6) | Atlan API error on DataProduct upsert | `DATAPRODUCT_UPSERT_FAILED` | Check Atlan API connectivity and permissions |
| 17 | Business Terms (Phase 2) | Atlan API error on term upsert | `TERM_UPSERT_FAILED` | Check glossary permissions |
| 18 | Business Terms (Phase 7) | Term association fails after all retries | `TERM_ASSOCIATION_FAILED` | Check Atlan API permissions; retry the provisioning request |
| 19 | Entity Reference | No asset found for `componentId` | `ENTITY_NOT_FOUND` | Verify the component was provisioned and its Output Port URN matches |
| 20 | Entity Reference | Atlan API error during search | `ENTITY_REFERENCE_LOOKUP_FAILED` | Check Atlan API connectivity and permissions |
| 21 | Any phase | Any other exception (raw Atlan SDK/HTTP error without one of the codes above) | `UNEXPECTED_ERROR` | Check the logs for the underlying exception; not a specific, actionable code today |
