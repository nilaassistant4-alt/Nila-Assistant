"""Security policy layer: permission brokering and egress classification.

Deliberately separate from ``brain/identity`` (who you are) and
``brain/crypto`` (primitives). This package answers a different question:
*what is this session allowed to touch, and what may leave the device.*
"""
from .permission import (  # noqa: F401
    CAPABILITIES, CAP_EXPORT, CAP_FILE_LIST, CAP_FILE_READ, CAP_FILE_WRITE,
    CAP_NET, CAP_PHOTO, CAP_SHELL, Broker, Decision, Grant, PermissionError_,
)
