from unittest import mock

import pytest
from pyatlan.errors import ErrorCode, NotFoundError
from pyatlan.model.assets import AtlasGlossaryTerm, View

from src.services.term_association_service import TermAssociationError, associate_business_terms


def _not_found_error() -> NotFoundError:
    return ErrorCode.ASSET_NOT_FOUND_BY_GUID.exception_with_parameters("guid-1")


def test_associate_business_terms_is_noop_when_no_terms():
    client = mock.MagicMock()

    associate_business_terms(client, View, "guid-1", [])

    client.append_terms.assert_not_called()


def test_associate_business_terms_calls_append_terms():
    client = mock.MagicMock()
    term = mock.MagicMock(spec=AtlasGlossaryTerm)

    associate_business_terms(client, View, "guid-1", [term])

    client.append_terms.assert_called_once_with(asset_type=View, terms=[term], guid="guid-1")


def test_associate_business_terms_wraps_atlan_api_error():
    client = mock.MagicMock()
    client.append_terms.side_effect = RuntimeError("boom")
    term = mock.MagicMock(spec=AtlasGlossaryTerm)

    with pytest.raises(TermAssociationError):
        associate_business_terms(client, View, "guid-1", [term])


@mock.patch("tenacity.nap.time.sleep")
def test_associate_business_terms_retries_on_not_found_then_succeeds(mock_sleep):
    client = mock.MagicMock()
    client.append_terms.side_effect = [_not_found_error(), _not_found_error(), None]
    term = mock.MagicMock(spec=AtlasGlossaryTerm)

    associate_business_terms(client, View, "guid-1", [term])

    assert client.append_terms.call_count == 3


@mock.patch("tenacity.nap.time.sleep")
def test_associate_business_terms_raises_after_exhausting_retries_on_persistent_not_found(mock_sleep):
    client = mock.MagicMock()
    client.append_terms.side_effect = _not_found_error()
    term = mock.MagicMock(spec=AtlasGlossaryTerm)

    with pytest.raises(TermAssociationError):
        associate_business_terms(client, View, "guid-1", [term])

    assert client.append_terms.call_count == 10

