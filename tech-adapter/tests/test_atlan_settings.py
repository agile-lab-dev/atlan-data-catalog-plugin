import os
from typing import Any
from unittest import mock

from src.clients.atlan_client_factory import build_atlan_client
from src.settings.atlan_settings import AtlanSettings


def _minimal_settings(**overrides: Any) -> AtlanSettings:
    # Isolated from any real application.yaml in the working directory (whose fields
    # would otherwise get deep-merged into these defaults-focused tests by
    # pydantic-settings, polluting them with local/tenant-specific values).
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com",
        oauth_client_id="client-id",
        oauth_client_secret="client-secret",
    )
    defaults.update(overrides)
    with mock.patch.dict(os.environ, {"ATLAN_CONFIG_FILE": "/nonexistent/application.yaml"}):
        return AtlanSettings(_env_file=None, **defaults)


def test_settings_defaults_are_applied():
    settings = _minimal_settings()

    assert settings.connection_mapping == {}
    assert settings.domain_mapping == {}
    assert settings.custom_metadata == {"type-name": "Witboost", "auto-create": True}
    assert settings.unprovision == {"strategy": "archive"}


def test_settings_loaded_from_env_vars():
    env = {
        "ATLAN_BASE_URL": "https://tenant.atlan.com",
        "ATLAN_OAUTH_CLIENT_ID": "id-from-env",
        "ATLAN_OAUTH_CLIENT_SECRET": "secret-from-env",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        settings = AtlanSettings()

    assert settings.base_url == "https://tenant.atlan.com"
    assert settings.oauth_client_id == "id-from-env"
    assert settings.oauth_client_secret == "secret-from-env"


def test_connection_qualified_name_matches_case_insensitively():
    settings = _minimal_settings(
        connection_mapping={
            "databricks": {
                "production": "default/databricks/1692345678",
                "development": "default/databricks/1692345999",
            }
        }
    )

    assert settings.connection_qualified_name("Databricks", "production") == "default/databricks/1692345678"
    assert settings.connection_qualified_name("DATABRICKS", "development") == "default/databricks/1692345999"
    assert settings.connection_qualified_name("databricks", "Development") == "default/databricks/1692345999"
    assert settings.connection_qualified_name("databricks", "PRODUCTION") == "default/databricks/1692345678"


def test_connection_qualified_name_returns_none_when_environment_not_configured():
    settings = _minimal_settings(connection_mapping={"databricks": {"production": "default/databricks/1692345678"}})

    assert settings.connection_qualified_name("databricks", "development") is None


def test_connection_qualified_name_returns_none_when_not_configured():
    settings = _minimal_settings(connection_mapping={"databricks": {"production": "qn"}})

    assert settings.connection_qualified_name("snowflake", "production") is None
    assert settings.connection_qualified_name("databricks", "staging") is None


def test_domain_qualified_name_uses_mapping_when_present():
    settings = _minimal_settings(domain_mapping={"agilefinance": "finance"})

    assert settings.domain_qualified_name("agilefinance") == "finance"


def test_domain_qualified_name_falls_back_to_domain_name():
    settings = _minimal_settings(domain_mapping={"agilefinance": "finance"})

    assert settings.domain_qualified_name("marketing") == "marketing"


def test_build_atlan_client_uses_oauth_credentials():
    settings = _minimal_settings()

    client = build_atlan_client(settings)

    assert str(client.base_url).rstrip("/") == "https://tenant.atlan.com"
    assert client.oauth_client_id == "client-id"
    assert client.oauth_client_secret == "client-secret"
    assert client.api_key is None


def _isolated_env(**atlan_overrides: str) -> dict[str, str]:
    """
    Builds a full environment dict with every ambient `ATLAN_*` variable stripped
    (this dev machine's shell may already export real tenant credentials) and the
    given overrides applied, for use with `mock.patch.dict(os.environ, ..., clear=True)`
    — this guarantees these tests only ever see the values they set, never a real
    tenant's ambient configuration. Callers must additionally pass `_env_file=None`
    when instantiating `AtlanSettings`, since the dotenv source reads the developer's
    local (git-ignored) `tech-adapter/.env` straight from disk, bypassing `os.environ`,
    and outranks the YAML config source.
    """  # noqa: E501
    base = {key: value for key, value in os.environ.items() if not key.startswith("ATLAN_")}
    base.update(atlan_overrides)
    return base


def test_settings_loaded_from_yaml_config_file(tmp_path):
    config_file = tmp_path / "application.yaml"
    config_file.write_text(
        "base_url: https://tenant-from-yaml.atlan.com\n"
        "connection_mapping:\n"
        "  databricks:\n"
        "    production: default/databricks/123\n"
        "unprovision:\n"
        "  strategy: purge\n"
    )
    env = _isolated_env(
        ATLAN_CONFIG_FILE=str(config_file),
        ATLAN_OAUTH_CLIENT_ID="id-from-env",
        ATLAN_OAUTH_CLIENT_SECRET="secret-from-env",
    )
    with mock.patch.dict(os.environ, env, clear=True):
        settings = AtlanSettings(_env_file=None)

    assert settings.base_url == "https://tenant-from-yaml.atlan.com"
    assert settings.connection_mapping == {"databricks": {"production": "default/databricks/123"}}
    assert settings.unprovision == {"strategy": "purge"}
    # secrets never live in the YAML file — always from the environment.
    assert settings.oauth_client_id == "id-from-env"
    assert settings.oauth_client_secret == "secret-from-env"


def test_environment_variable_overrides_yaml_config_file(tmp_path):
    config_file = tmp_path / "application.yaml"
    config_file.write_text("base_url: https://tenant-from-yaml.atlan.com\n")
    env = _isolated_env(
        ATLAN_CONFIG_FILE=str(config_file),
        ATLAN_BASE_URL="https://tenant-from-env.atlan.com",
        ATLAN_OAUTH_CLIENT_ID="id-from-env",
        ATLAN_OAUTH_CLIENT_SECRET="secret-from-env",
    )
    with mock.patch.dict(os.environ, env, clear=True):
        settings = AtlanSettings(_env_file=None)

    assert settings.base_url == "https://tenant-from-env.atlan.com"


def test_missing_yaml_config_file_falls_back_to_env_and_defaults(tmp_path):
    missing_file = tmp_path / "does-not-exist.yaml"
    env = _isolated_env(
        ATLAN_CONFIG_FILE=str(missing_file),
        ATLAN_BASE_URL="https://tenant.atlan.com",
        ATLAN_OAUTH_CLIENT_ID="id-from-env",
        ATLAN_OAUTH_CLIENT_SECRET="secret-from-env",
    )
    with mock.patch.dict(os.environ, env, clear=True):
        settings = AtlanSettings(_env_file=None)

    assert settings.base_url == "https://tenant.atlan.com"
    assert settings.connection_mapping == {}
    assert settings.unprovision == {"strategy": "archive"}


def test_helm_files_application_yaml_matches_settings_schema():
    """
    Guards helm/files/application.yaml — the canonical default (non-secret)
    configuration shipped via the Helm chart's ConfigMap (see
    helm/templates/configmap.yaml) — against silently drifting out of sync with
    AtlanSettings' actual fields (docs/technical-details.md §Configuration).
    """  # noqa: E501
    import yaml

    example_path = os.path.join(os.path.dirname(__file__), "..", "..", "helm", "files", "application.yaml")
    with open(example_path) as f:
        example_config = yaml.safe_load(f)

    env = _isolated_env(
        ATLAN_CONFIG_FILE=example_path,
        ATLAN_OAUTH_CLIENT_ID="id-from-env",
        ATLAN_OAUTH_CLIENT_SECRET="secret-from-env",
    )
    with mock.patch.dict(os.environ, env, clear=True):
        settings = AtlanSettings(_env_file=None)

    for key in example_config:
        assert key in AtlanSettings.model_fields, f"helm/files/application.yaml has an unknown field: {key}"
    assert settings.base_url == example_config["base_url"]
