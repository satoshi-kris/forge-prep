"""Single source of truth for the installed package version — no hardcoded version strings elsewhere."""

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    try:
        return version("forge-prep")
    except PackageNotFoundError:
        return "0+unknown"
