from __future__ import annotations

from loguru import logger

from src.models.catalog_asset import CatalogAssetDescriptor
from src.models.data_product_descriptor import OutputPort
from src.resolvers.base import (
    ConnectionNotConfiguredError,
    OutputPortResolver,
    ResolverContext,
)
from src.resolvers.databricks import DatabricksOutputPortResolver
from src.settings.atlan_settings import AtlanSettings


class OutputPortResolverRegistry:
    """
    Selects the `OutputPortResolver` for a given Output Port by technology
    (case-insensitive), and resolves the target Connection qualifiedName from
    configuration — see docs/technical-details.md §Phase 1: Output Port Resolution.

    Output Ports whose technology has no registered resolver are skipped (return
    `None`), never raised as an error: the Data Catalog Plugin is additive with
    respect to the deploy and must not fail the whole request for components it does
    not yet catalog (same contract as `validate`).
    """  # noqa: E501

    def __init__(self, resolvers: list[OutputPortResolver] | None = None) -> None:
        self._resolvers = resolvers if resolvers is not None else [DatabricksOutputPortResolver()]

    def resolve(
        self,
        output_port: OutputPort,
        settings: AtlanSettings,
        environment: str,
    ) -> CatalogAssetDescriptor | None:
        """
        Resolves the given Output Port to a `CatalogAssetDescriptor`, or returns
        `None` when no resolver supports its technology (skip, do not fail).

        Raises:
            ConnectionNotConfiguredError: a resolver supports the technology but no
                Connection qualifiedName is configured for it in this environment.
            IdentityResolutionFailedError: propagated from the resolver when identity
                cannot be computed (e.g. missing platform-specific coordinates).
        """  # noqa: E501
        resolver = self._select_resolver(output_port)
        if resolver is None:
            logger.info(
                "Skipping Output Port '{}': unsupported technology '{}'",
                output_port.id,
                output_port.technology,
            )
            return None

        technology = (output_port.technology or "").strip()
        connection_qualified_name = settings.connection_qualified_name(technology, environment)
        if connection_qualified_name is None:
            raise ConnectionNotConfiguredError(
                f"No Atlan Connection configured for technology '{technology}' and "
                f"environment '{environment}' (Output Port '{output_port.id}')",
                output_port_id=output_port.id,
            )

        context = ResolverContext(
            connection_qualified_name=connection_qualified_name,
            environment=environment,
        )
        return resolver.resolve(output_port, context)

    def _select_resolver(self, output_port: OutputPort) -> OutputPortResolver | None:
        for resolver in self._resolvers:
            if resolver.supports(output_port):
                return resolver
        return None
