# Technologies with a registered Output Port resolver (see docs/technical-details.md
# §Output Port Resolver Design). Matched case-insensitively against the descriptor's
# `technology` field.
SUPPORTED_TECHNOLOGIES = ["Databricks"]

# Fields under `specific` that must be present (and non-empty) on every Databricks
# Output Port. These are the OP's OWN Unity Catalog coordinates, used to resolve the
# Output Port's Atlan asset identity/qualifiedName in Phase 1 (see
# docs/technical-details.md §Databricks Resolver) — distinct from the
# `catalogName`/`schemaName`/`tableName` fields, which describe the upstream source
# table only (lineage input) and are not required for identity resolution.
DATABRICKS_REQUIRED_SPECIFIC_FIELDS = ["catalogNameOP", "schemaNameOP", "viewNameOP"]

# The 9 attributes of the "Witboost" Custom Metadata type (see
# docs/technical-details.md §Configuration → custom-metadata and §Phase 2:
# Prerequisite Metadata Definitions). All attributes are plain strings; attribute id
# resolution (never hardcode Atlan's generated 22-character ids) is delegated to
# pyatlan's own CustomMetadataCache/CustomMetadataDict at write time (Phase 5).
CUSTOM_METADATA_ATTRIBUTES = [
    "Data Product URN",
    "Data Product Version",
    "Domain",
    "Environment",
    "Output Port URN",
    "Data Product Owner",
    "Dev Group",
    "Witboost Link",
    "Last Synced At",
]

OPENMETADATA_SUPPORTED_DATATYPES = [
    "NUMBER",
    "TINYINT",
    "SMALLINT",
    "INT",
    "BIGINT",
    "BYTEINT",
    "BYTES",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "NUMERIC",
    "TIMESTAMP",
    "TIMESTAMPZ",
    "TIME",
    "DATE",
    "DATETIME",
    "INTERVAL",
    "STRING",
    "MEDIUMTEXT",
    "TEXT",
    "CHAR",
    "LONG",
    "VARCHAR",
    "BOOLEAN",
    "BINARY",
    "VARBINARY",
    "ARRAY",
    "BLOB",
    "LONGBLOB",
    "MEDIUMBLOB",
    "MAP",
    "STRUCT",
    "UNION",
    "SET",
    "GEOGRAPHY",
    "ENUM",
    "JSON",
    "UUID",
    "VARIANT",
    "GEOMETRY",
    "POINT",
    "POLYGON",
    "BYTEA",
]
