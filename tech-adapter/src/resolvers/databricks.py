from __future__ import annotations

from src.models.catalog_asset import (
    AssetIdentity,
    CatalogAssetDescriptor,
    CatalogColumnDescriptor,
)
from src.models.data_product_descriptor import OutputPort
from src.resolvers.base import (
    IdentityResolutionFailedError,
    OutputPortResolver,
    ResolverContext,
)

_TECHNOLOGY = "databricks"


class DatabricksOutputPortResolver(OutputPortResolver):
    """
    Resolves Databricks Output Ports to Atlan `View` assets under a Unity
    Catalog-shaped Database/Schema hierarchy (docs/technical-details.md §Databricks
    Resolver).

    Identity is built exclusively from the Output Port's OWN Unity Catalog
    coordinates (`specific.catalogNameOP` / `schemaNameOP` / `viewNameOP`), never
    from the upstream source coordinates (`specific.catalogName` / `schemaName` /
    `tableName`), which are lineage-only (see docs/reference-descriptor.yaml header).
    """  # noqa: E501

    def supports(self, output_port: OutputPort) -> bool:
        technology = (output_port.technology or "").strip().lower()
        return technology == _TECHNOLOGY

    def resolve(self, output_port: OutputPort, context: ResolverContext) -> CatalogAssetDescriptor:
        catalog_name = output_port.specific.get("catalogNameOP")
        schema_name = output_port.specific.get("schemaNameOP")
        view_name = output_port.specific.get("viewNameOP")

        missing = [
            field
            for field, value in (
                ("catalogNameOP", catalog_name),
                ("schemaNameOP", schema_name),
                ("viewNameOP", view_name),
            )
            if not value
        ]
        if missing:
            raise IdentityResolutionFailedError(
                f"Output Port '{output_port.id}' is missing required 'specific' fields for "
                f"Databricks identity resolution: {missing}",
                output_port_id=output_port.id,
            )

        database_qn = f"{context.connection_qualified_name}/{catalog_name}"
        schema_qn = f"{database_qn}/{schema_name}"
        asset_qn = f"{schema_qn}/{view_name}"

        columns = [
            CatalogColumnDescriptor(
                name=column.name,
                data_type=column.databricks_data_type(),
                description=column.description,
            )
            for column in (output_port.dataContract.schema_ or [])
        ]

        return CatalogAssetDescriptor(
            connection_identity=AssetIdentity(type_name="Connection", qualified_name=context.connection_qualified_name),
            asset_identity=AssetIdentity(type_name="View", qualified_name=asset_qn),
            schema_identity=AssetIdentity(type_name="Schema", qualified_name=schema_qn),
            database_identity=AssetIdentity(type_name="Database", qualified_name=database_qn),
            name=view_name,
            description=output_port.description,
            columns=columns,
        )
