from typing import Type, TypeVar

import pydantic
import yaml
from loguru import logger
from pydantic import BaseModel

from src.models.api_models import ValidationError

T = TypeVar("T", bound=BaseModel)


def parse_yaml_with_model(yaml_data: dict | str, model: Type[T]) -> T | ValidationError:
    """
    Parse YAML data using a Pydantic model.

    This function takes either a dictionary containing YAML data or a YAML string,
    along with a Pydantic model class as input. It attempts to create an instance
    of the provided Pydantic model using the data from the input.

    Args:
        yaml_data (dict | str): YAML data to be parsed. This can be either a dictionary
            or a YAML string.
        model (Type[T]): The Pydantic model class to use for parsing.

    Returns:
        T | ValidationError: An instance of the Pydantic model with data from yaml_data,
            or a ValidationError if a validation error occurs during parsing.

    Example:
        You can use either a dictionary or a YAML string as input:

        1. Using a dictionary:

        >>> yaml_data = {
        ...     "title": "Example",
        ...     "description": "This is an example"
        ... }
        >>> result = parse_yaml_with_model(yaml_data, ModelB)
        >>> print(result)
        ModelB(title='Example', description='This is an example')

        2. Using a YAML string:

        >>> yaml_data = '''
        ... title: Another Example
        ... description: This is another example
        ... '''
        >>> result = parse_yaml_with_model(yaml_data, ModelB)
        >>> print(result)
        ModelB(title='Another Example', description='This is another example')

    Raises:
        Exception: If an unexpected error occurs during parsing.
    """  # noqa: E501
    try:
        if isinstance(yaml_data, str):
            yaml_dict = yaml.safe_load(yaml_data)
        else:
            yaml_dict = yaml_data

        data = model(**yaml_dict)
        return data
    except pydantic.ValidationError as ve:
        error_msg = "Failed to parse the descriptor. Details: \n"
        logger.exception(error_msg)
        combined = [
            error_msg
            + " , \n".join(
                map(
                    str,
                    ve.errors(include_url=False, include_context=False, include_input=False),
                )
            )
        ]
        return ValidationError(errors=combined)
    except Exception as e:
        logger.exception("Unexpected error")
        raise e
