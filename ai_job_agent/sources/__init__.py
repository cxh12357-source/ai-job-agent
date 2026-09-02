"""Read-only connectors for public non-Greenhouse ATS job boards."""

from .ashby import AshbyAdapter, AshbySource
from .base import (
    HTTPSession,
    JobSourceAdapter,
    SourceDiscoveryResult,
    SourceFailure,
    SourceRequestError,
    clean_html,
)
from .lever import LeverAdapter, LeverSource
from .liauto_campus import LiAutoCampusAdapter, LiAutoCampusSource
from .generic_official import (
    GenericOfficialSource,
    OfficialSourceError,
    SourceDiscoveryResult as GenericSourceDiscoveryResult,
    discover_official_jobs,
)
from .rendered_official import (
    RenderedDiscoveryResult,
    RenderedOfficialError,
    discover_rendered_jobs,
)
from .smartrecruiters import SmartRecruitersAdapter, SmartRecruitersSource

__all__ = [
    "AshbyAdapter",
    "AshbySource",
    "HTTPSession",
    "JobSourceAdapter",
    "GenericOfficialSource",
    "GenericSourceDiscoveryResult",
    "LeverAdapter",
    "LeverSource",
    "LiAutoCampusAdapter",
    "LiAutoCampusSource",
    "OfficialSourceError",
    "RenderedDiscoveryResult",
    "RenderedOfficialError",
    "SmartRecruitersAdapter",
    "SmartRecruitersSource",
    "SourceDiscoveryResult",
    "SourceFailure",
    "SourceRequestError",
    "clean_html",
    "discover_official_jobs",
    "discover_rendered_jobs",
]
