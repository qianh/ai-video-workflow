"""Media adapters: real CLI/local tools with honest degrade policy."""

from .base import MediaResult
from .policy import allow_mock, mock_reason

__all__ = ["MediaResult", "allow_mock", "mock_reason"]
