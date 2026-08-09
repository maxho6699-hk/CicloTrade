"""Compatibility helpers for worker modules."""

from enum import Enum

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised on Python 3.10
    class StrEnum(str, Enum):
        """Python 3.10 equivalent of :class:`enum.StrEnum` for explicit values."""

        def __str__(self) -> str:
            return str.__str__(self)

        __format__ = str.__format__
