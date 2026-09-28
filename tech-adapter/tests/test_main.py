from pathlib import Path
from unittest import mock

from fastapi.encoders import jsonable_encoder
from starlette.testclient import TestClient

from src.clients.atlan_client_factory import get_atlan_client, get_atlan_settings
from src.main import app
from src.models.api_models import (
    DescriptorKind,
    EntityReference,
    Info,
    ProvisionInfo,
    ProvisioningRequest,
    ProvisioningStatus,
    Status1,
    SystemErr,
    UpdateAclRequest,
)

client = TestClient(app)

# /v1/provision and /v1/unprovision depend on a real AtlanClient/AtlanSettings via
# FastAPI DI (see src/main.py `AtlanClientDep`/`AtlanSettingsDep`) — overridden here
# with test doubles so these tests never attempt a real OAuth exchange or network
# call; the orchestrators themselves (`provision_data_product`/
# `unprovision_data_product`) are unit-tested in isolation in
# tests/test_provision_service.py / tests/test_unprovision_service.py.
app.dependency_overrides[get_atlan_client] = lambda: mock.MagicMock()
app.dependency_overrides[get_atlan_settings] = lambda: mock.MagicMock()


def test_provisioning_invalid_descriptor():
    provisioning_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS, descriptor="descriptor"
    )

    resp = client.post("/v1/provision", json=dict(provisioning_request))

    assert resp.status_code == 400
    assert "Unable to parse the descriptor." in resp.json().get("errors")


@mock.patch("src.main.provision_data_product")
def test_provisioning_valid_descriptor(mock_provision):
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()

    provisioning_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS, descriptor=descriptor_str
    )

    mock_provision.return_value = ProvisioningStatus(
        status=Status1.COMPLETED, result="ok", info=Info(publicInfo={}, privateInfo={})
    )

    resp = client.post("/v1/provision", json=dict(provisioning_request))

    assert resp.status_code == 200
    assert resp.json().get("status") == "COMPLETED"
    mock_provision.assert_called_once()


@mock.patch("src.main.provision_data_product")
def test_provisioning_valid_descriptor_wraps_orchestrator_error(mock_provision):
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()

    provisioning_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS, descriptor=descriptor_str
    )

    mock_provision.return_value = SystemErr(error="ASSET_UPSERT_FAILED: boom")

    resp = client.post("/v1/provision", json=dict(provisioning_request))

    assert resp.status_code == 500
    assert "ASSET_UPSERT_FAILED" in resp.json().get("error")


def test_unprovisioning_invalid_descriptor():
    unprovisioning_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS, descriptor="descriptor"
    )

    resp = client.post("/v1/unprovision", json=dict(unprovisioning_request))

    assert resp.status_code == 400
    assert "Unable to parse the descriptor." in resp.json().get("errors")


@mock.patch("src.main.unprovision_data_product")
def test_unprovisioning_valid_descriptor(mock_unprovision):
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()

    unprovisioning_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR_WITH_RESULTS, descriptor=descriptor_str
    )

    mock_unprovision.return_value = ProvisioningStatus(
        status=Status1.COMPLETED, result="ok", info=Info(publicInfo={}, privateInfo={})
    )

    resp = client.post("/v1/unprovision", json=dict(unprovisioning_request))

    assert resp.status_code == 200
    assert resp.json().get("status") == "COMPLETED"
    mock_unprovision.assert_called_once()


def test_validate_invalid_descriptor():
    validate_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR, descriptor="descriptor"
    )

    resp = client.post("/v1/validate", json=dict(validate_request))

    assert resp.status_code == 200
    assert "Unable to parse the descriptor." in resp.json().get("error").get("errors")


def test_validate_descriptor_with_unsupported_technology_is_skipped():
    # descriptor_output_port_valid.yaml declares technology: Snowflake, which has no
    # registered resolver yet (only Databricks is currently supported). The Data
    # Catalog Plugin is additive: it must not block validation for components it
    # simply does not catalog yet, so this Output Port is silently skipped.
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()

    validate_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR, descriptor=descriptor_str
    )

    resp = client.post("/v1/validate", json=dict(validate_request))

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("valid") is True
    assert body.get("error") is None


def test_validate_valid_databricks_descriptor():
    descriptor_str = Path("tests/descriptors/descriptor_databricks_valid.yaml").read_text()

    validate_request = ProvisioningRequest(
        descriptorKind=DescriptorKind.DATAPRODUCT_DESCRIPTOR, descriptor=descriptor_str
    )

    resp = client.post("/v1/validate", json=dict(validate_request))

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("valid") is True
    assert body.get("error") is None


def test_updateacl_invalid_descriptor():
    updateacl_request = UpdateAclRequest(
        provisionInfo=ProvisionInfo(request="descriptor", result=""),
        refs=["user:alice", "user:bob"],
    )

    resp = client.post("/v1/updateacl", json=jsonable_encoder(updateacl_request))

    assert resp.status_code == 400
    assert "Unable to parse the descriptor." in resp.json().get("errors")


def test_updateacl_valid_descriptor():
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()

    updateacl_request = UpdateAclRequest(
        provisionInfo=ProvisionInfo(request=descriptor_str, result=""),
        refs=["user:alice", "user:bob"],
    )

    resp = client.post("/v1/updateacl", json=jsonable_encoder(updateacl_request))

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "COMPLETED"
    assert "not applicable" in body.get("result")


@mock.patch("src.main.get_entity_reference")
def test_entity_reference_found(mock_get_entity_reference):
    mock_get_entity_reference.return_value = EntityReference(
        reference="https://tenant.atlan.com/assets/guid-1/overview"
    )

    resp = client.get("/v1/entity/reference", params={"componentId": "urn:dmb:cmp:finance:risk-finance:0:op"})

    assert resp.status_code == 200
    assert resp.json().get("reference") == "https://tenant.atlan.com/assets/guid-1/overview"
    mock_get_entity_reference.assert_called_once()


@mock.patch("src.main.get_entity_reference")
def test_entity_reference_not_found(mock_get_entity_reference):
    mock_get_entity_reference.return_value = SystemErr(error="ENTITY_NOT_FOUND: no Atlan asset found for componentId")

    resp = client.get("/v1/entity/reference", params={"componentId": "urn:dmb:cmp:finance:risk-finance:0:missing"})

    assert resp.status_code == 500
    assert "ENTITY_NOT_FOUND" in resp.json().get("error")


def test_entity_reference_requires_component_id():
    resp = client.get("/v1/entity/reference")

    assert resp.status_code == 422
