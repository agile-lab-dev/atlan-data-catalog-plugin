from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from src.models.catalog_asset import CatalogAssetDescriptor
from src.models.data_product_descriptor import OutputPort


class ResolverError(Exception):
    """
    Base class for Phase 1 (Output Port Resolution) failures. Callers (the
    provisioning orchestrator) are expected to catch this and translate it into the
    corresponding `SystemErr`/`ValidationError` code — see `code` below and
    docs/technical-details.md §Phase 1: Output Port Resolution.
    """

    code: str = "IDENTITY_RESOLUTION_FAILED"

    def __init__(self, message: str, output_port_id: str) -> None:
        super().__init__(message)
        self.output_port_id = output_port_id


class ConnectionNotConfiguredError(ResolverError):
    """Raised when no Atlan Connection qualifiedName is configured for the Output Port's technology + environment (docs/technical-details.md §Phase 1, step 5)."""  # noqa: E501

    code = "CONNECTION_NOT_CONFIGURED"


class IdentityResolutionFailedError(ResolverError):
    """Raised when the resolver cannot compute a valid Atlan identity for the Output Port, e.g. missing platform-specific coordinates (docs/technical-details.md §Phase 1, step 6)."""  # noqa: E501

    code = "IDENTITY_RESOLUTION_FAILED"


class ResolverContext(BaseModel):
    """
    Per-Output-Port context passed to `OutputPortResolver.resolve()`, carrying
    everything resolved from configuration ahead of time (docs/technical-details.md
    §Output Port Resolver Design).
    """

    connection_qualified_name: str
    environment: str
    asset_mapping_config: dict = {}


class OutputPortResolver(ABC):
    """Resolves an Output Port descriptor component to its Atlan asset identity (docs/technical-details.md §Output Port Resolver Design)."""  # noqa: E501

    @abstractmethod
    def supports(self, output_port: OutputPort) -> bool:
        """Returns True if this resolver handles the given Output Port's technology."""
        ...

    @abstractmethod
    def resolve(self, output_port: OutputPort, context: ResolverContext) -> CatalogAssetDescriptor:
        """
        Resolves the Output Port to an Atlan asset identity + column list.

        Raises:
            IdentityResolutionFailedError: identity cannot be computed (missing
                required platform-specific coordinates).
        """
        ...
