from __future__ import annotations

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.errors import NotFoundError
from pyatlan.model.assets import AtlasGlossary, AtlasGlossaryTerm

from src.settings.atlan_settings import AtlanSettings


class GlossaryNotFoundError(Exception):
    """Raised when the configured glossary does not exist and `glossary.create-if-missing` is disabled (docs/technical-details.md §Phase 2, step 3d)."""  # noqa: E501

    code = "GLOSSARY_NOT_FOUND"


class TermUpsertError(Exception):
    """Raised when a Business Term cannot be searched for or created in Atlan."""

    code = "TERM_UPSERT_FAILED"


def resolve_glossary_name(settings: AtlanSettings, domain: str) -> str:
    """
    Resolves which Atlan glossary name a Business Term for the given Witboost
    domain should live in, per `glossary.strategy` (docs/technical-details.md
    §glossary):
    - `shared`: uses the single configured `shared-glossary-name`.
    - `per-domain`: looks up `domain` in `glossary.glossary-mapping` (Witboost
      domain -> Atlan glossary name, mirroring `domain_mapping`); if not mapped,
      falls back to `glossary.default-glossary-name` when configured, or to the
      Witboost domain name as-is otherwise.
    """  # noqa: E501
    strategy = settings.glossary.get("strategy", "per-domain")
    if strategy == "shared":
        return settings.glossary.get("shared-glossary-name", "Witboost")

    glossary_mapping = settings.glossary.get("glossary-mapping", {})
    if domain in glossary_mapping:
        return glossary_mapping[domain]

    return settings.glossary.get("default-glossary-name") or domain


def ensure_glossary(client: AtlanClient, settings: AtlanSettings, glossary_name: str) -> AtlasGlossary:
    """
    Finds the glossary by name, creating it if missing and
    `glossary.create-if-missing` is enabled (docs/technical-details.md §Phase 2,
    step 3; §glossary).

    Raises:
        GlossaryNotFoundError: the glossary does not exist and
            `create-if-missing` is disabled.
    """  # noqa: E501
    try:
        return client.find_glossary_by_name(glossary_name)
    except NotFoundError:
        pass

    if not settings.glossary.get("create-if-missing", True):
        raise GlossaryNotFoundError(
            f"Glossary '{glossary_name}' not found in Atlan and 'glossary.create-if-missing' is disabled"
        )  # noqa: E501

    logger.info("Creating glossary '{}' in Atlan", glossary_name)
    glossary = AtlasGlossary.creator(name=glossary_name)
    response = client.save(glossary)
    created = response.assets_created(AtlasGlossary)
    if not created:
        raise TermUpsertError(f"Failed to create glossary '{glossary_name}': no asset returned by Atlan")
    return created[0]


def ensure_business_term(
    client: AtlanClient,
    settings: AtlanSettings,
    glossary_name: str,
    term_name: str,
) -> AtlasGlossaryTerm:
    """
    Ensures a Business Term exists in the given glossary, reusing it (without
    overwriting its description) when found, or creating it otherwise
    (docs/technical-details.md §Phase 2, step 3a-c).

    Raises:
        GlossaryNotFoundError: propagated from `ensure_glossary` when the term is
            not found and the glossary itself does not exist / cannot be created.
        TermUpsertError: the term cannot be searched for or created due to an Atlan
            API error.
    """  # noqa: E501
    try:
        return client.find_term_by_name(name=term_name, glossary_name=glossary_name)
    except NotFoundError:
        pass

    glossary = ensure_glossary(client, settings, glossary_name)

    logger.info("Creating Business Term '{}' in glossary '{}'", term_name, glossary_name)
    try:
        term = AtlasGlossaryTerm.creator(name=term_name, glossary_qualified_name=glossary.qualified_name)
        response = client.save(term)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise TermUpsertError(
            f"Failed to create Business Term '{term_name}' in glossary '{glossary_name}': {exc}"
        ) from exc  # noqa: E501

    created = response.assets_created(AtlasGlossaryTerm)
    if not created:
        raise TermUpsertError(f"Failed to create Business Term '{term_name}': no asset returned by Atlan")
    return created[0]
