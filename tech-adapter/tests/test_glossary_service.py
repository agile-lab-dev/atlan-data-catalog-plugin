from typing import Any
from unittest import mock

import pytest
from pyatlan.errors import ErrorCode
from pyatlan.model.assets import AtlasGlossary, AtlasGlossaryTerm

from src.services.glossary_service import (
    GlossaryNotFoundError,
    TermUpsertError,
    ensure_business_term,
    ensure_glossary,
    resolve_glossary_name,
)
from src.settings.atlan_settings import AtlanSettings


def _settings(**overrides: Any) -> AtlanSettings:
    # Isolated from any real application.yaml in the working directory (whose
    # `glossary` block would otherwise get deep-merged with the `glossary`
    # override below by pydantic-settings, polluting these unit tests with
    # local/tenant-specific values).
    defaults: dict[str, Any] = dict(
        base_url="https://tenant.atlan.com", oauth_client_id="id", oauth_client_secret="secret"
    )
    defaults.update(overrides)
    with mock.patch.dict("os.environ", {"ATLAN_CONFIG_FILE": "/nonexistent/application.yaml"}):
        return AtlanSettings(**defaults)


def _not_found_error():
    return ErrorCode.API_TOKEN_NOT_FOUND_BY_NAME.exception_with_parameters("x")


def test_resolve_glossary_name_per_domain_uses_domain():
    settings = _settings(glossary={"strategy": "per-domain"})

    assert resolve_glossary_name(settings, "finance") == "finance"


def test_resolve_glossary_name_per_domain_uses_glossary_mapping_when_present():
    settings = _settings(glossary={"strategy": "per-domain", "glossary-mapping": {"finance": "Finance Glossary"}})

    assert resolve_glossary_name(settings, "finance") == "Finance Glossary"


def test_resolve_glossary_name_per_domain_falls_back_to_default_glossary_name():
    settings = _settings(
        glossary={
            "strategy": "per-domain",
            "glossary-mapping": {"finance": "Finance Glossary"},
            "default-glossary-name": "Witboost",
        }
    )

    assert resolve_glossary_name(settings, "marketing") == "Witboost"


def test_resolve_glossary_name_per_domain_falls_back_to_domain_when_no_default():
    settings = _settings(glossary={"strategy": "per-domain", "glossary-mapping": {"finance": "Finance Glossary"}})

    assert resolve_glossary_name(settings, "marketing") == "marketing"


def test_resolve_glossary_name_shared_uses_configured_name():
    settings = _settings(glossary={"strategy": "shared", "shared-glossary-name": "Witboost"})

    assert resolve_glossary_name(settings, "finance") == "Witboost"


def test_ensure_glossary_returns_existing_when_found():
    client = mock.MagicMock()
    existing = mock.MagicMock(spec=AtlasGlossary)
    client.find_glossary_by_name.return_value = existing

    result = ensure_glossary(client, _settings(), "finance")

    assert result is existing
    client.save.assert_not_called()


def test_ensure_glossary_creates_when_missing_and_allowed():
    client = mock.MagicMock()
    client.find_glossary_by_name.side_effect = _not_found_error()
    created_glossary = mock.MagicMock(spec=AtlasGlossary)
    client.save.return_value.assets_created.return_value = [created_glossary]

    result = ensure_glossary(client, _settings(glossary={"create-if-missing": True}), "finance")

    assert result is created_glossary
    client.save.assert_called_once()


def test_ensure_glossary_raises_when_missing_and_not_allowed():
    client = mock.MagicMock()
    client.find_glossary_by_name.side_effect = _not_found_error()

    with pytest.raises(GlossaryNotFoundError):
        ensure_glossary(client, _settings(glossary={"create-if-missing": False}), "finance")


def test_ensure_business_term_returns_existing_when_found():
    client = mock.MagicMock()
    existing = mock.MagicMock(spec=AtlasGlossaryTerm)
    client.find_term_by_name.return_value = existing

    result = ensure_business_term(client, _settings(), "finance", "Order Identifier")

    assert result is existing
    client.save.assert_not_called()


def test_ensure_business_term_creates_when_missing():
    client = mock.MagicMock()
    client.find_term_by_name.side_effect = _not_found_error()
    client.find_glossary_by_name.side_effect = _not_found_error()
    created_glossary = mock.MagicMock(spec=AtlasGlossary, qualified_name="finance")
    created_term = mock.MagicMock(spec=AtlasGlossaryTerm)

    # First save() call creates the glossary, second creates the term.
    glossary_response = mock.MagicMock()
    glossary_response.assets_created.return_value = [created_glossary]
    term_response = mock.MagicMock()
    term_response.assets_created.return_value = [created_term]
    client.save.side_effect = [glossary_response, term_response]

    result = ensure_business_term(
        client, _settings(glossary={"create-if-missing": True}), "finance", "Order Identifier"
    )

    assert result is created_term
    assert client.save.call_count == 2


def test_ensure_business_term_propagates_glossary_not_found():
    client = mock.MagicMock()
    client.find_term_by_name.side_effect = _not_found_error()
    client.find_glossary_by_name.side_effect = _not_found_error()

    with pytest.raises(GlossaryNotFoundError):
        ensure_business_term(client, _settings(glossary={"create-if-missing": False}), "finance", "Order Identifier")


def test_ensure_business_term_wraps_atlan_api_error_on_create():
    client = mock.MagicMock()
    client.find_term_by_name.side_effect = _not_found_error()
    client.find_glossary_by_name.return_value = mock.MagicMock(spec=AtlasGlossary, qualified_name="finance")
    client.save.side_effect = RuntimeError("boom")

    with pytest.raises(TermUpsertError):
        ensure_business_term(client, _settings(), "finance", "Order Identifier")
