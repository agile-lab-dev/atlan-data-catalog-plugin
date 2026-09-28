import unittest
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from starlette.testclient import TestClient

from src.dependencies import (
    UnpackedProvisioningRequestDep,
    UnpackedUpdateAclRequestDep,
    unpack_provisioning_request,
    unpack_update_acl_request,
    unpack_validation_request,
)
from src.models.api_models import (
    ProvisionInfo,
    ProvisioningRequest,
    UpdateAclRequest,
    ValidationError,
)
from src.models.data_product_descriptor import DataProduct


class TestUnpackUpdateAclRequest(unittest.IsolatedAsyncioTestCase):
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()
    update_acl_request = UpdateAclRequest(
        refs=["user:testuser", "bigData"],
        provisionInfo=ProvisionInfo(
            request=descriptor_str,  # noqa: E501
            result="result_prov",
        ),
    )

    async def test_successful_unpack(self):
        result = await unpack_update_acl_request(self.update_acl_request)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        self.assertIsInstance(result[0], DataProduct)
        self.assertEqual(result[1], "urn:dmb:dp:healthcare:vaccinations:0")
        self.assertEqual(result[2], self.update_acl_request.refs)

    async def test_invalid_request(self):
        # Create a mock UpdateAclRequest instance with an invalid request
        update_acl_request = Mock()
        update_acl_request.provisionInfo.request = "Invalid JSON"

        # Call the function and assert the result
        result = await unpack_update_acl_request(update_acl_request)
        self.assertIsInstance(result, ValidationError)
        self.assertIn("Unable to parse the descriptor.", result.errors[0])

    async def test_exception_handling(self):
        update_acl_request = Mock()
        update_acl_request.provisionInfo.request = "{}"

        result = await unpack_update_acl_request(update_acl_request)
        self.assertIsInstance(result, ValidationError)


class TestUnpackProvisioningRequest(unittest.IsolatedAsyncioTestCase):
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()
    provisioning_request = ProvisioningRequest(
        descriptorKind="DATAPRODUCT_DESCRIPTOR_WITH_RESULTS",
        descriptor=descriptor_str,  # noqa: E501
    )

    invalid_provisioning_request = ProvisioningRequest(
        # dropped the 'name' field from the previous provisioning_request
        descriptorKind="DATAPRODUCT_DESCRIPTOR_WITH_RESULTS",
        descriptor=descriptor_str.replace("name: Vaccinations", "invalid_field: Invalid Value"),  # noqa: E501
    )

    async def test_successful_unpack(self):
        result = await unpack_provisioning_request(self.provisioning_request)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], DataProduct)
        self.assertEqual(result[1], "urn:dmb:dp:healthcare:vaccinations:0")

    async def test_invalid_request(self):
        result = await unpack_provisioning_request(self.invalid_provisioning_request)
        self.assertIsInstance(result, ValidationError)
        self.assertIn("Failed to parse the descriptor. Details:", result.errors[0])

    async def test_exception_handling(self):
        provisioning_request = Mock()
        provisioning_request.descriptorKind = "DATAPRODUCT_DESCRIPTOR_WITH_RESULTS"
        provisioning_request.descriptor = "Invalid JSON"

        result = await unpack_provisioning_request(provisioning_request)
        self.assertIsInstance(result, ValidationError)


class TestUnpackValidationRequest(unittest.IsolatedAsyncioTestCase):
    descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()
    validation_request = ProvisioningRequest(
        descriptorKind="DATAPRODUCT_DESCRIPTOR",
        descriptor=descriptor_str,  # noqa: E501
    )

    async def test_successful_unpack(self):
        result = await unpack_validation_request(self.validation_request)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], DataProduct)
        self.assertEqual(result[1], "urn:dmb:dp:healthcare:vaccinations:0")

    async def test_rejects_with_results_kind(self):
        # /v1/validate runs before any provisioning happened, so it must not accept
        # the enriched DATAPRODUCT_DESCRIPTOR_WITH_RESULTS kind expected by
        # unpack_provisioning_request.
        request = ProvisioningRequest(
            descriptorKind="DATAPRODUCT_DESCRIPTOR_WITH_RESULTS",
            descriptor=self.descriptor_str,
        )

        result = await unpack_validation_request(request)
        self.assertIsInstance(result, ValidationError)
        self.assertIn("Expecting a DATAPRODUCT_DESCRIPTOR", result.errors[0])


app_test = FastAPI()


@app_test.post("/provision")
async def provision(data: UnpackedProvisioningRequestDep):
    if isinstance(data, tuple) and len(data) == 2:
        data_product, component_id = data
        return {
            "message": "Provisioning completed successfully",
            "data_product": data_product,
            "component_id": component_id,
        }
    elif isinstance(data, ValidationError):
        return {"message": "Provisioning failed", "errors": data.errors}


@app_test.post("/updateacl")
async def update_acl(data: UnpackedUpdateAclRequestDep):
    if isinstance(data, tuple) and len(data) == 3:
        data_product, component_id, refs = data
        return {
            "message": "Update acl completed successfully",
            "data_product": data_product,
            "component_id": component_id,
        }
    elif isinstance(data, ValidationError):
        return {"message": "Provisioning failed", "errors": data.errors}


client = TestClient(app_test)


class TestAppDependenciesMock(unittest.TestCase):
    def test_provision_valid_request(self):
        descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()
        valid_provisioning_request = ProvisioningRequest(
            descriptorKind="DATAPRODUCT_DESCRIPTOR_WITH_RESULTS",
            descriptor=descriptor_str,  # noqa: E501
        )

        response = client.post("/provision", json=valid_provisioning_request.model_dump())

        assert response.status_code == 200
        assert "Provisioning completed successfully" in response.json()["message"]
        assert "data_product" in response.json()
        assert "component_id" in response.json()

    def test_provision_invalid_request(self):
        invalid_provisioning_request = ProvisioningRequest(
            descriptorKind="DATAPRODUCT_DESCRIPTOR_WITH_RESULTS", descriptor="invalid_descriptor"
        )

        response = client.post("/provision", json=invalid_provisioning_request.model_dump())

        assert response.status_code == 200
        assert "Provisioning failed" in response.json()["message"]
        assert "errors" in response.json()

    def test_updateacl_valid_request(self):
        descriptor_str = Path("tests/descriptors/descriptor_output_port_valid.yaml").read_text()
        valid_update_acl_request = UpdateAclRequest(
            refs=["user:testuser", "bigData"],
            provisionInfo=ProvisionInfo(
                request=descriptor_str,  # noqa: E501
                result="result_prov",
            ),
        )

        response = client.post("/updateacl", json=valid_update_acl_request.model_dump())

        assert response.status_code == 200
        assert "Update acl completed successfully" in response.json()["message"]
        assert "data_product" in response.json()
        assert "component_id" in response.json()

    def test_updateacl_invalid_request(self):
        invalid_provisioning_request = UpdateAclRequest(
            refs=["user:testuser", "bigData"],
            provisionInfo=ProvisionInfo(
                request="invalid_request",
                result="result_prov",
            ),
        )

        response = client.post("/updateacl", json=invalid_provisioning_request.model_dump())

        assert response.status_code == 200
        assert "Provisioning failed" in response.json()["message"]
        assert "errors" in response.json()
