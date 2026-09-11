"""Importing this package registers every transport.

The engine only ever calls base.build(name, ...); importing the concrete
modules here is what populates the registry, so nothing else has to know the
module names.
"""

from . import http_push, sftp, taildrop  # noqa: F401  (registration side effect)
from .base import Transport, TransferResult, available, build, register

__all__ = ["Transport", "TransferResult", "available", "build", "register"]
