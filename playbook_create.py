"""TcEx Framework Module"""

# standard library
import base64
import json
import logging
import os
from collections.abc import Callable, Iterable
from typing import Any

# third-party
from pydantic import BaseModel

from ...app.key_value_store import KeyValueRedis
from ...app.key_value_store.key_value_store import KeyValueStore
from ...util.util import Util

# get logger
_logger = logging.getLogger(__name__.split('.', maxsplit=1)[0])


class PlaybookCreate:
    """Playbook Write ABC"""

    def __init__(
        self,
        context: str,
        key_value_store: KeyValueStore,
        output_variables: list,
    ):
        """Initialize the class properties."""
        self.context = context
        self.key_value_store = key_value_store
        self.output_variables = output_variables

        # properties
        self.log = _logger
        self.util = Util()

    @staticmethod
    def _check_iterable(value: dict | Iterable | str, validate: bool):
        """Raise an exception if value is not an Iterable.

        Validation:
          - not a dict (dicts are iterable)
          - not a string (strings are iterable)
          - is Iterable
        """
        if validate is True and (isinstance(value, dict | str) or not isinstance(value, Iterable)):
            ex_msg = 'Invalid data provided for KeyValueArray.'
            raise RuntimeError(ex_msg)

    def _check_null(self, key: str, value: Any) -> bool:
        """Return True if key or value is null."""
        invalid = False

        # this code should be unreachable, but just in case
        if key is None:
            self.log.warning('The provided key was None.')
            invalid = True

        if value is None:
            self.log.warning(f'The provided value for key {key} was None.')
            invalid = True

            # specifically to allow the tcex-test framework to validate outputs
            if os.getenv('TC_PLAYBOOK_WRITE_NULL') is not None and isinstance(
                self.key_value_store.client, KeyValueRedis
            ):
                variable = self._get_variable(key)
                self.log.debug(f'event=writing-null-to-kvstore, variable={variable}')
                self.key_value_store.redis_client.hset(
                    self.context, f'{variable}_NULL_VALIDATION', ''
                )

        return invalid

    def _check_requested(self, variable: str, when_requested: bool) -> bool:
        """Return True if output variable was requested by downstream app."""
        if when_requested is True and not self.is_requested(variable):
            self.log.debug(f'Variable {variable} was NOT requested by downstream app.')
            return False
        return True

    def _check_variable_type(self, variable: str, type_: str):
        """Validate the correct type was passed to the method."""
        if self.util.get_playbook_variable_type(variable).lower() != type_.lower():
            ex_msg = f'Invalid variable provided ({variable}), variable must be of type {type_}.'
            raise RuntimeError(ex_msg)

    @staticmethod
    def _coerce_string_value(value: bool | float | str) -> str:
        """Return a string value from an bool or int."""
        # coerce bool before int as python says a bool is an int
        if isinstance(value, bool):
            # coerce bool to str type
            value = str(value).lower()

        # coerce int to str type
        if isinstance(value, float | int):
            value = str(value)

        return value

    def _create_data(self, key: str, value: bytes | str) -> int | None:
        """Write data to key value store."""
        self.log.debug(f'writing variable {key.strip()}')
        try:
            return self.key_value_store.client.create(self.context, key.strip(), value)
        except RuntimeError:  # pragma: no cover
            self.log.exception('Error writing data to key value store.')
            return None

    def _get_variable(self, key: str, variable_type: str | None = None) -> str | None:
        """Return properly formatted variable.

        A key can be provided as the variable key (e.g., app.output) or the
        entire (e.g., #App:1234:app.output!String). The full variable is required
        to create the record in the KV Store.

        If a variable_type is provided an exact match will be found, however if no
        variable type is known the first key to match will be returned. Uniqueness
        of keys is not guaranteed, but in more recent Apps it is the standard.

        If no variable is found it means that the variable was not requested by the
        any downstream Apps or could possible be formatted incorrectly.
        """
        if not self.util.is_playbook_variable(key):
            # try to lookup the variable in the requested output variables.
            for output_variable in self.output_variables:
                variable_model = self.util.get_playbook_variable_model(output_variable)
                if (
                    variable_model
                    and variable_model.key == key
                    and (variable_type is None or variable_model.type == variable_type)
                ):
                    # either an exact match, or first match
                    return output_variable

            # not requested by downstream App or misconfigured
            return None

        # key was already a properly formatted variable
        return key

    @staticmethod
    def _serialize_data(value: dict | list | str) -> str:
        """Get the value from Redis if applicable."""
        try:
            return json.dumps(value)
        except ValueError as ex:  # pragma: no cover
            ex_msg = f'Invalid data provided, failed to serialize value ({ex}).'
            raise RuntimeError(ex_msg) from ex

    @staticmethod
    def _process_object_types(
        value: BaseModel | dict,
        validate: bool = True,
        allow_none: bool = False,
    ) -> dict[str, Any]:
        """Process object types (e.g., KeyValue, TCEntity)."""
        types = (BaseModel, dict)
        if allow_none is True:
            types = (BaseModel, dict, type(None))

        if validate and not isinstance(value, types):
            ex_msg = f'Invalid type provided for object type ({type(value)}).'
            raise RuntimeError(ex_msg)

        if isinstance(value, BaseModel):
            value = value.dict(exclude_unset=True)

        return value

    @staticmethod
    def is_key_value(data: dict) -> bool:
        """Return True if provided data has proper structure for Key Value."""
        if not isinstance(data, dict):
            return False
        return all(x in data for x in ['key', 'value'])

    def is_requested(self, variable: str) -> bool:
        """Return True if provided variable was requested by downstream App."""
        return variable in self.output_variables

    @staticmethod
    def is_tc_batch(data: dict) -> bool:
        """Return True if provided data has proper structure for TC Batch."""
        if not isinstance(data, dict):
            return False
        if not isinstance(data.get('indicator', []), list):
            return False
        return isinstance(data.get('group', []), list)

    @staticmethod
    def is_tc_entity(data: dict) -> bool:
        """Return True if provided data has proper structure for TC Entity."""
        if not isinstance(data, dict):
            return False
        return all(x in data for x in ['id', 'value', 'type'])

    def any(
        self,
        key: str,
        value: (
            BaseModel | bytes | dict | str | list[BaseModel] | list[bytes] | list[dict] | list[str]
        ),
        validate: bool = True,
        variable_type: str | None = None,
        when_requested: bool = True,
    ) -> int | None:
        """Write the value to the keystore for all types.

        This is a quick helper method, for more advanced features
        the individual write methods should be used (e.g., binary).

        Args:
            key: The variable to write to the DB (e.g., app.colors).
            value: The data to write to the DB.
            validate: Perform validation on the data.
            variable_type: The variable type being written. Only required if not unique.
            when_requested: Only write the data if the variable was requested by downstream App.
        """
        if self._check_null(key, value) is True:
            return None

        # convert key to variable if required
        variable = self._get_variable(key, variable_type)
        if variable is None:
            # variable is invalid or not requested by downstream App
            return None

        if self._check_requested(variable, when_requested) is False:
            # variable is not requested by downstream App
            return None

        # get the type from the variable
        variable_type = self.util.get_playbook_variable_type(variable).lower()

        # map type to create method
        variable_type_map: dict[str, Callable] = {
            'binary': self.binary,
            'binaryarray': self.binary_array,
            'keyvalue': self.key_value,
            'keyvaluearray': self.key_value_array,
            'string': self.string,
            'stringarray': self.string_array,
            'tcentity': self.tc_entity,
            'tcentityarray': self.tc_entity_array,
            'tcbatch': self.tc_batch,
        }
        return variable_type_map.get(variable_type, self.raw)(  # type: ignore
            variable,
            value,
            validate,
            when_requested,  # type: ignore
        )

    def binary(
        self,
        key: str,
        value: bytes,
        validate: bool = True,
        when_requested: bool = True,
    ) -> int | None:
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # convert key to variable if required
        variable = self._get_variable(key, 'Binary')
        if variable is None:
            # variable is invalid or not requested by downstream App
            return None

        if self._check_requested(variable, when_requested) is False:
            # variable is not requested by downstream App
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'Binary')

        # basic validation of value
        if validate and not isinstance(value, bytes):
            ex_msg = 'Invalid data provided for Binary.'
            raise RuntimeError(ex_msg)

        # prepare value - playbook Binary fields are base64 encoded
        value_ = base64.b64encode(value).decode('utf-8')
        value_ = self._serialize_data(value_)
        return self._create_data(variable, value_)

    def binary_array(
        self,
        key: str,
        value: list[bytes],
        validate: bool = True,
        when_requested: bool = True,
    ) -> int | None:
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # validate array type provided
        self._check_iterable(value, validate)

        # convert key to variable if required
        variable = self._get_variable(key, 'BinaryArray')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'BinaryArray')

        # basic validation and prep of value
        value_encoded = []
        for v in value:
            v_ = v
            if v_ is not None:
                if validate and not isinstance(v_, bytes):
                    ex_msg = 'Invalid data provided for Binary.'
                    raise RuntimeError(ex_msg)
                v_ = base64.b64encode(v_).decode('utf-8')
            value_encoded.append(v_)
        return self._create_data(variable, self._serialize_data(value_encoded))

    def key_value(
        self,
        key: str,
        value: BaseModel | dict,
        validate: bool = True,
        when_requested: bool = True,
    ) -> int | None:
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # convert key to variable if required
        variable = self._get_variable(key, 'KeyValue')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'KeyValue')

        # basic validation and prep of value
        value = self._process_object_types(value, validate)
        if validate and not self.is_key_value(value):
            ex_msg = 'Invalid data provided for KeyValueArray.'
            raise RuntimeError(ex_msg)

        return self._create_data(variable, self._serialize_data(value))

    def key_value_array(
        self,
        key: str,
        value: list[BaseModel | dict],
        validate: bool = True,
        when_requested: bool = True,
    ):
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # validate array type provided
        self._check_iterable(value, validate)

        # convert key to variable if required
        variable = self._get_variable(key, 'KeyValueArray')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'KeyValueArray')

        # basic validation and prep of value
        _value = []
        for v in value:
            v_ = self._process_object_types(v, validate, allow_none=True)
            if validate and not self.is_key_value(v_):
                ex_msg = 'Invalid data provided for KeyValueArray.'
                raise RuntimeError(ex_msg)
            _value.append(v_)
        value = _value

        return self._create_data(variable, self._serialize_data(value))

    def string(
        self,
        key: str,
        value: bool | float | str,
        validate: bool = True,
        when_requested: bool = True,
    ) -> int | None:
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # convert key to variable if required
        variable = self._get_variable(key, 'String')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'String')

        # coerce string values
        value = self._coerce_string_value(value)

        # validation only needs to check str because value was coerced
        if validate and not isinstance(value, str):
            ex_msg = f'Invalid data provided for String ({value}).'
            raise RuntimeError(ex_msg)

        return self._create_data(variable, self._serialize_data(value))

    def string_array(
        self,
        key: str,
        value: list[bool | float | int | str],
        validate: bool = True,
        when_requested: bool = True,
    ):
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # validate array type provided
        self._check_iterable(value, validate)

        # convert key to variable if required
        variable = self._get_variable(key, 'StringArray')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'StringArray')

        # basic validation and prep of value
        value_coerced = []
        for v in value:
            # coerce string values
            v_ = self._coerce_string_value(v)

            # validation only needs to check str because value was coerced
            if validate and not isinstance(v_, type(None) | str):
                ex_msg = 'Invalid data provided for StringArray.'
                raise RuntimeError(ex_msg)
            value_coerced.append(v_)
        value = value_coerced

        return self._create_data(variable, self._serialize_data(value))

    def raw(
        self,
        key: str,
        value: bytes | str,
        _validate: bool = True,
        _when_requested: bool = True,
    ) -> int | None:
        """Create method of CRUD operation for raw data.

        Raw data can only be a byte, str or int. Other data
        structures (dict, list, etc) must be serialized.
        """
        if self._check_null(key, value):
            return None

        return self._create_data(key, value)

    def tc_batch(
        self,
        key: str,
        value: BaseModel | dict,
        validate: bool = True,
        when_requested: bool = True,
    ) -> int | None:
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # convert key to variable if required
        variable = self._get_variable(key, 'TCBatch')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'TCBatch')

        # basic validation
        value = self._process_object_types(value, validate)
        if validate and not self.is_tc_batch(value):
            ex_msg = 'Invalid data provided for TcBatch.'
            raise RuntimeError(ex_msg)

        return self._create_data(variable, self._serialize_data(value))

    def tc_entity(
        self,
        key: str,
        value: BaseModel | dict,
        validate: bool = True,
        when_requested: bool = True,
    ) -> int | None:
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # convert key to variable if required
        variable = self._get_variable(key, 'TCEntity')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'TCEntity')

        # basic validation
        value = self._process_object_types(value, validate)
        if validate and not self.is_tc_entity(value):
            ex_msg = 'Invalid data provided for TcEntityArray.'
            raise RuntimeError(ex_msg)

        return self._create_data(variable, self._serialize_data(value))

    def tc_entity_array(
        self,
        key: str,
        value: list[BaseModel | dict],
        validate: bool = True,
        when_requested: bool = True,
    ):
        """Create the value in Redis if applicable."""
        if self._check_null(key, value) is True:
            return None

        # validate array type provided
        self._check_iterable(value, validate)

        # convert key to variable if required
        variable = self._get_variable(key, 'TCEntityArray')
        if variable is None:
            return None

        if self._check_requested(variable, when_requested) is False:
            return None

        # quick check to ensure an invalid type was not provided
        self._check_variable_type(variable, 'TCEntityArray')

        # basic validation and prep of value
        _value = []
        for v in value:
            v_ = self._process_object_types(v, validate, allow_none=True)
            if validate and not self.is_tc_entity(v_):
                ex_msg = 'Invalid data provided for TcEntityArray.'
                raise RuntimeError(ex_msg)
            _value.append(v_)
        value = _value

        return self._create_data(variable, self._serialize_data(value))

    def variable(
        self,
        key: str,
        value: (
            BaseModel
            | bytes
            | dict
            | int
            | str
            | list
            | list[BaseModel | None]
            | list[bytes | None]
            | list[dict | None]
            | list[str | None]
            | None
        ),
        variable_type: str | None = None,
    ) -> int | None:
        """Alias for any method of CRUD operation for working with KeyValue DB.

        This method will automatically check to see if provided variable was requested by
        a downstream app and if so create the data in the KeyValue DB.

        Args:
            key: The variable to write to the DB (e.g., app.colors).
            value: The data to write to the DB.
            variable_type: The variable type being written. Only required if not unique.
        """
        if self._check_null(key, value) is True:
            return None

        # short-circuit the process, if there are no downstream variables requested.
        if not self.output_variables:  # pragma: no cover
            self.log.debug(f'Variable {key} was NOT requested by downstream app.')
            return None

        # key can be provided as the variable key (e.g., app.output) or
        # the entire (e.g., #App:1234:app.output!String). we need the
        # full variable to proceed.
        variable = self._get_variable(key, variable_type)
        if variable is None:
            return None

        if variable is None or variable not in self.output_variables:
            self.log.debug(f'Variable {key} was NOT requested by downstream app.')
            return None

        # write the variable (None value would be caught in _check_null method)
        return self.any(variable, value)  # type: ignore
