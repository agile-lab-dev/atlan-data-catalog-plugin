from __future__ import annotations

import os

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource


class AtlanSettings(BaseSettings):
    """
    Operator-owned configuration for the Atlan Data Catalog Plugin.

    See docs/technical-details.md §Configuration for the full field reference.
    Non-secret fields (everything except `oauth_client_id`/`oauth_client_secret`) can
    be set in a YAML config file (default `config/application.yaml`, relative to the
    working directory the service is started from — this is the path a Kubernetes
    ConfigMap volume mounts the file at, see helm/templates/configmap.yaml and
    helm/templates/deployment.yaml; overridable via the `ATLAN_CONFIG_FILE`
    environment variable — see `settings_customise_sources` below);     a full example lives in `config/application-sample.yaml` (mirrored, for Helm
    deployments, by helm/files/application.yaml). Secrets must always be injected via
    the environment
    (or a local `.env` file for development, never committed) —
    `ATLAN_OAUTH_CLIENT_ID` / `ATLAN_OAUTH_CLIENT_SECRET`. Any field may also be
    overridden via its `ATLAN_<FIELD_NAME>` environment variable, which always takes
    precedence over the YAML file.
    """  # noqa: E501

    base_url: str
    oauth_client_id: str
    oauth_client_secret: str

    # technology -> environment -> Atlan Connection qualifiedName (see §connection_mapping)
    connection_mapping: dict[str, dict[str, str]] = {}

    # Witboost domain -> Atlan DataDomain qualifiedName (see §domain_mapping).
    # Fallback (undocumented domains use the domain name as-is) is applied by callers.
    domain_mapping: dict[str, str] = {}

    # see §custom-metadata
    custom_metadata: dict = {"type-name": "Witboost", "auto-create": True}

    # see §glossary
    glossary: dict = {
        "strategy": "per-domain",
        "shared-glossary-name": "Witboost",
        "create-if-missing": True,
        "glossary-mapping": {},
        "default-glossary-name": None,
    }

    # see §unprovision
    unprovision: dict = {"strategy": "archive"}

    # see §retry
    retry: dict = {
        "max-attempts": 3,
        "backoff-multiplier": 2,
        "initial-delay-ms": 1000,
    }

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ATLAN_",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Adds `config/application.yaml` (or `$ATLAN_CONFIG_FILE`, re-read on every
        instantiation rather than frozen at import time, so tests can point at a
        different file per case) as a settings source, ranked below environment
        variables and `.env` so a real environment variable always wins over the file
        for the same field — this is what lets secrets stay environment-only even if
        the file is checked in or shared. Ranked above the field defaults, so
        anything not set in the file/environment still falls back to the documented
        defaults. A missing file is not an error: it is treated as an empty source
        and every field falls through to `.env`/environment/defaults.
        """  # noqa: E501
        yaml_file = os.environ.get("ATLAN_CONFIG_FILE", "config/application.yaml")
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file),
            file_secret_settings,
        )

    def connection_qualified_name(self, technology: str, environment: str) -> str | None:
        """
        Resolves the Atlan Connection qualifiedName for the given technology and
        environment, matching both `technology` and `environment` case-insensitively
        (descriptor values such as `Databricks` must match a `databricks` key, and
        `Development` must match a `development` key, in `connection_mapping`).

        Returns None if the technology or the environment within it is not configured.
        """  # noqa: E501
        for configured_technology, per_environment in self.connection_mapping.items():
            if configured_technology.lower() == technology.lower():
                for configured_environment, qualified_name in per_environment.items():
                    if configured_environment.lower() == environment.lower():
                        return qualified_name
                return None
        return None

    def domain_qualified_name(self, domain: str) -> str:
        """
        Resolves the Atlan DataDomain qualifiedName for the given Witboost domain.

        Falls back to the domain name as-is when not present in `domain_mapping`
        (see §domain_mapping fallback behavior).
        """  # noqa: E501
        return self.domain_mapping.get(domain, domain)
