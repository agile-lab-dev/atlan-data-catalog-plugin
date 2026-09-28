# Required Atlan Permissions — Service Principal

This document lists the permissions to grant, in Atlan, to the service
principal used by the Witboost Data Catalog Plugin for Atlan.

## Authentication

The data catalog plugin connects to Atlan as a single service principal, authenticated
via **OAuth 2.0 Client Credentials** (client ID + client secret). All
permissions below must be granted to that service principal, scoped to:

- the **connections** where cataloged data assets (databases, schemas,
  tables/views, columns) live;
- the **glossaries** used for Business Terms;
- the **domains** used for Data Products.

Connections, glossaries and domains themselves are expected to already exist
in Atlan (pre-provisioned by your team) — the data catalog plugin only reads them and
manages the objects inside them.

## Permissions to assign

| #  | What the data catalog plugin needs to do | Permission to grant | Scope | Why |
|----|---|---|---|---|
| 1  | Search and view assets, glossaries, terms, domains and data products | **View/Read assets** | Connections, glossaries, domains in scope | Before creating or changing anything, the adapter always checks whether the item already exists |
| 2  | Create and update databases, schemas, tables/views and columns | **Create assets**, **Update assets** | Connections in scope | Cataloging a data product creates/updates its metadata structure in Atlan |
| 3  | Archive assets that are removed from a data product | **Delete assets** (archive) | Connections in scope | Columns/tables/data products removed from a component are archived so Atlan stays in sync |
| 4  | Permanently remove a Data Product on de-provisioning (if hard-delete is configured) | **Purge assets** (permanent delete) | Domains in scope | When a data product is fully decommissioned, its Atlan Data Product asset can be permanently deleted instead of just archived |
| 5  | Restore a previously archived asset | **Restore assets** | Connections and domains in scope | Re-provisioning a component whose asset was archived restores it instead of duplicating it |
| 6  | Create glossaries and business terms | **Create/manage glossaries and terms** | Glossaries in scope | The adapter creates one Business Term per data product/output port for documentation purposes |
| 7  | Link business terms to data assets | **Update assets** (manage related terms) | Connections and glossaries in scope | Associates the created business term with the corresponding data asset |
| 8  | Create a custom metadata structure to store Witboost-specific attributes | **Manage custom metadata (typedefs)** | Tenant-wide (admin-level capability, not connection-scoped) | The first time it runs, the adapter creates a small custom metadata structure to store Witboost identifiers (component id, domain, environment, etc.), if not already present |
| 9  | Set custom metadata values on assets | **Update custom metadata** | Connections in scope | Writes the Witboost identifiers into the custom metadata fields on the assets it manages |
| 10 | Read the target domain for each Data Product | **Read domains** | Domains in scope | Domains are not created by the adapter; it only needs to find and validate the configured one |
| 11 | Create and update Data Products, including their linked assets | **Create Data Products**, **Update Data Products** | Domains in scope | Materializes each Witboost Data Product as an Atlan Data Product |


## Nice to have (optional) permissions

| # | What the data catalog plugin needs to do | Permission to grant | Scope | Why                                                                                           |
|---|---|---|---|-----------------------------------------------------------------------------------------------|
| 1 | Look up Atlan groups by name | **Read groups** | Tenant-wide (admin-level capability) | Needed to grant/revoke Data Product ownership to specific teams/groups                        |
| 2 | Look up Atlan users by email | **Read users** | Tenant-wide (admin-level capability) | Needed to grant/revoke Data Product ownership to specific people |
