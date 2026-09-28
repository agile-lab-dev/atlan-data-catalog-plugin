from __future__ import annotations

from loguru import logger

from src.models.api_models import ValidationError, ValidationResult
from src.models.constants import (
    DATABRICKS_REQUIRED_SPECIFIC_FIELDS,
    SUPPORTED_TECHNOLOGIES,
)
from src.models.data_product_descriptor import DataProduct, OutputPort

_SUPPORTED_TECHNOLOGIES_LOWER = {tech.lower() for tech in SUPPORTED_TECHNOLOGIES}


def validate_data_product(data_product: DataProduct) -> ValidationResult:
    """
    Runs the descriptor-only checks documented in docs/technical-details.md §Validation
    against the full Data Product descriptor. No Atlan API call is performed.

    Output Ports whose `technology` has no registered resolver (see
    `SUPPORTED_TECHNOLOGIES`) are skipped: they are not cataloged by this plugin, so
    they are neither validated further nor reported as a failure. This mirrors the
    Data Catalog Plugin contract, which is additive with respect to the deploy
    (docs/HLD.md §Overview) and must not block provisioning for components it simply
    does not catalog yet.

    Args:
        data_product (DataProduct): the parsed Data Product descriptor to validate.

    Returns:
        ValidationResult: `valid=True` if every check passes, otherwise `valid=False`
        with the list of `VALIDATION_*` failure codes/messages collected.
    """  # noqa: E501

    errors: list[str] = []

    if not data_product.name or not data_product.name.strip():
        errors.append("VALIDATION_MISSING_DP_NAME: the Data Product 'name' field is missing or empty")

    if not data_product.domain or not data_product.domain.strip():
        errors.append("VALIDATION_MISSING_DOMAIN: the Data Product 'domain' field is missing or empty")

    output_ports = data_product.get_output_ports()

    if not output_ports:
        errors.append("VALIDATION_NO_OUTPUT_PORTS: the Data Product must contain at least one Output Port component")  # noqa: E501
    else:
        for output_port in output_ports:
            if not _is_supported_technology(output_port):
                logger.info(
                    "Skipping validation of Output Port '{}': unsupported technology '{}'",
                    output_port.id,
                    output_port.technology,
                )
                continue
            errors.extend(_validate_output_port(output_port))

    if errors:
        return ValidationResult(valid=False, error=ValidationError(errors=errors))

    return ValidationResult(valid=True)


def _is_supported_technology(output_port: OutputPort) -> bool:
    technology = (output_port.technology or "").strip()
    return technology.lower() in _SUPPORTED_TECHNOLOGIES_LOWER


def _validate_output_port(output_port: OutputPort) -> list[str]:
    errors: list[str] = []

    technology = (output_port.technology or "").strip().lower()

    if technology == "databricks":
        missing_fields = [field for field in DATABRICKS_REQUIRED_SPECIFIC_FIELDS if not output_port.specific.get(field)]
        if missing_fields:
            errors.append(
                "VALIDATION_INCOMPLETE_METADATA: Output Port "
                f"'{output_port.id}' is missing required 'specific' fields: {missing_fields}"
            )

    if not output_port.dataContract.schema_:
        errors.append(
            "VALIDATION_MISSING_SCHEMA: Output Port "
            f"'{output_port.id}' has an empty or missing 'dataContract.schema'"
        )

    return errors
