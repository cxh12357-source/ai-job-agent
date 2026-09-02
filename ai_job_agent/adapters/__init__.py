"""Career-site adapters that only create reviewable plans."""

from .base import (
    AdapterError,
    ApplyCandidate,
    ApplyDiscoveryPlan,
    BaseCareerAdapter,
    LinkSnapshot,
    UnsafeCareerUrl,
)
from .generic import GenericAdapter

__all__ = [
    "AdapterError",
    "ApplyCandidate",
    "ApplyDiscoveryPlan",
    "BaseCareerAdapter",
    "GenericAdapter",
    "LinkSnapshot",
    "UnsafeCareerUrl",
]
