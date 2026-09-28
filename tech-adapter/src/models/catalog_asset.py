from __future__ import annotations

from pydantic import BaseModel


class AssetIdentity(BaseModel):
    """Atlan asset identity: all Atlan interactions key off `type_name` + `qualified_name`, never GUIDs (see docs/technical-details.md §Entity Identity and qualifiedName)."""  # noqa: E501

    type_name: str
    qualified_name: str


class CatalogColumnDescriptor(BaseModel):
    """
    Technology-agnostic representation of a single column, derived 1:1 from a
    `dataContract.schema` entry (`OpenMetadataColumn`). Only carries what Phase 1
    (Output Port Resolution) is responsible for producing — see
    docs/technical-details.md §Phase 1: Output Port Resolution ("Column list from
    dataContract.schema"); tag/business-term association happens later, in Phases 2
    and 7, directly against the original descriptor.
    """  # noqa: E501

    name: str
    data_type: str
    description: str | None = None


class CatalogAssetDescriptor(BaseModel):
    """
    Technology-agnostic representation of the Atlan asset (Table/View) an Output Port
    resolves to, as produced by an `OutputPortResolver.resolve()` (Phase 1). Consumed
    by the Atlan service layer in later provisioning phases (Phase 3 onward) — see
    docs/technical-details.md §Output Port Resolver Design.
    """  # noqa: E501

    connection_identity: AssetIdentity
    asset_identity: AssetIdentity
    schema_identity: AssetIdentity
    database_identity: AssetIdentity
    name: str
    description: str | None = None
    columns: list[CatalogColumnDescriptor] = []
