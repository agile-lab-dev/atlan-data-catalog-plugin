from __future__ import annotations

from loguru import logger
from pyatlan.client.atlan import AtlanClient
from pyatlan.errors import NotFoundError
from pyatlan.model.assets import Asset, AtlasGlossaryTerm
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class TermAssociationError(Exception):
    """Raised when Business Terms cannot be associated to an asset (docs/technical-details.md §Phase 7)."""  # noqa: E501

    code = "TERM_ASSOCIATION_FAILED"


@retry(
    reraise=True,
    retry=retry_if_exception_type(NotFoundError),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=1, max=5),
)
def _append_terms_with_retry(
    client: AtlanClient,
    asset_type: type[Asset],
    guid: str,
    terms: list[AtlasGlossaryTerm],
) -> None:
    client.append_terms(asset_type=asset_type, terms=terms, guid=guid)


def associate_business_terms(
    client: AtlanClient,
    asset_type: type[Asset],
    guid: str,
    terms: list[AtlasGlossaryTerm],
) -> None:
    """
    Phase 7: associates the given Business Terms to the asset (Table/View, Column,
    or DataProduct) identified by `guid`, via Atlan's `assignedTerms` relationship
    (docs/technical-details.md §Phase 7: Associate Business Terms).

    A no-op when `terms` is empty (an asset/column with no referenced `tagFQN`s).

    Uses `client.append_terms`, which adds to any terms already assigned rather than
    replacing them — safe to call on every provision without erasing terms assigned
    by a previous run.

    `client.append_terms` resolves the asset by GUID through a search-index query,
    which is only eventually consistent: an asset created or updated earlier in the
    same provisioning request (e.g. the DataProduct itself, just upserted in Phase 6)
    can briefly 404 here even though the save already succeeded. Retries with
    exponential backoff on `NotFoundError` to ride out that delay, the same way
    pyatlan's own native Atlan Tag operations do internally.

    Raises:
        TermAssociationError: any Atlan API failure while associating the terms,
            including a `NotFoundError` that is still unresolved after all retries.
    """  # noqa: E501
    if not terms:
        return

    logger.info("Associating {} Business Term(s) to asset '{}'", len(terms), guid)
    try:
        _append_terms_with_retry(client, asset_type, guid, terms)
    except Exception as exc:  # noqa: BLE001 - any Atlan API failure maps to the documented error code
        raise TermAssociationError(f"Failed to associate Business Terms to asset '{guid}': {exc}") from exc
