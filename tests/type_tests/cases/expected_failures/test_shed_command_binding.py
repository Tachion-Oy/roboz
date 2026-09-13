"""Command catalogs require executable resource implementations."""

from roboshed.tools import ExecutableCommandCatalog

# Expected: reportArgumentType; object is not an ExecutableDependency.
ExecutableCommandCatalog({"program": object()})
