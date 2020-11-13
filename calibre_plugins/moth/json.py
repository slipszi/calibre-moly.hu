"""JSON helper classes which help enforce strict typing."""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING, cast

import json
import calibre_plugins.moth.exceptions as exceptions


if TYPE_CHECKING:
    # pylint: disable=E0401
    from _typeshed import SupportsRead
    SupportsReadBytes = SupportsRead[bytes]
else:
    SupportsReadBytes = Any

JsonValueType = Union[None, bool, int, float, str, "JsonListType",
                      "JsonObjectType"]


# pylint: disable=R0903
class JsonListType(List[JsonValueType]):
    """Class which represents the type of a JSON list."""


# pylint: disable=R0903
class JsonObjectType(Dict[str, JsonValueType]):
    """Class which represents the type of a JSON object."""


class JsonObject:
    """Class which represents a JSON object."""

    @classmethod
    def from_stream(cls, stream: SupportsReadBytes) -> \
            JsonObject:
        """Initialize the JSON object from a stream.

        :param stream: The stream containing JSON data.
        """
        return cls(cast(JsonValueType, json.load(stream)))

    def __init__(self, value: JsonValueType) -> None:
        """Initialize the JSON object.

        :param json_object: The raw JSON object.
        """
        if not isinstance(value, dict):
            raise exceptions.JsonError("The JSON value is not an object")

        self._object: JsonObjectType = value

    def get_object(self, key: str) -> JsonObject:
        """Get the JSON object corresponding to the given key.

        :param key: The key to the value.
        """
        value = self._object.get(key)
        if value is None:
            raise LookupError("Cannot find \"%s\"" % key)

        if not isinstance(value, dict):
            raise exceptions.JsonError("\"%s\" is not an object" % key)

        return JsonObject(value)

    def get_list(self, key: str) -> List[JsonObject]:
        """Get the list corresponding to the given key.

        :param key: The key to the value.
        """
        values = self._object.get(key)
        if values is None:
            raise LookupError("Cannot find \"%s\"" % key)

        if not isinstance(values, list):
            raise exceptions.JsonError("\"%s\" is not a list" % key)

        return [JsonObject(value) for value in values]

    def get_optional_int(self, key: str) -> Optional[int]:
        """Get the integer value corresponding to the given key.

        :param key: The key to the value.
        """
        value = self._object.get(key)
        if value is None:
            return None

        if not isinstance(value, int):
            raise exceptions.JsonError("\"%s\" is not an integer" % key)

        return value

    def get_int(self, key: str) -> int:
        """Get the integer value corresponding to the given key.

        :param key: The key to the value.
        """
        value = self.get_optional_int(key)
        if value is None:
            raise LookupError("Cannot find \"%s\"" % key)

        return value

    def get_float(self, key: str) -> float:
        """Get the floating-point number value corresponding to the given key.

        :param key: The key to the value.
        """
        value = self._object.get(key)
        if value is None:
            raise LookupError("Cannot find \"%s\"" % key)

        if not isinstance(value, float):
            raise LookupError("\"%s\" is not an integer" % key)

        return value

    def get_optional_str(self, key: str) -> Optional[str]:
        """Get the string value corresponding to the given key.

        :param key: The key to the value.
        """
        value = self._object.get(key)
        if value is None:
            return None

        if not isinstance(value, str):
            raise exceptions.JsonError("\"%s\" is not a string" % key)

        return value

    def get_str(self, key: str) -> str:
        """Get the string value corresponding to the given key.

        :param key: The key to the value.
        """
        value = self.get_optional_str(key)
        if value is None:
            raise LookupError("Cannot find \"%s\"" % key)

        return value
