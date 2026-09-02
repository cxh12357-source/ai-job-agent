"""Offer a fixed, source-only local tool bundle; never package user files."""

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[2]
BUNDLE_FILES = (
    "local_scraper.py",
    "requirements-local-scraper.txt",
    "examples/local_scraper_config.json",
    "docs/LOCAL_SCRAPER.md",
)


def build_local_scraper_bundle() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for relative_name in BUNDLE_FILES:
            archive.writestr(relative_name, (ROOT / relative_name).read_bytes())
    return buffer.getvalue()
