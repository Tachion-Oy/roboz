"""Command catalogs require executable resource implementations."""

from roboz.shed.tools import ExecutableCommandCatalog

# Expected: reportArgumentType; object is not an ExecutableDependency.
ExecutableCommandCatalog({"program": object()})
