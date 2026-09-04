"""Errors raised by deterministic memory-maintenance tools."""


class LibrarianProviderRequestFailure(RuntimeError):
    """Terminal provider request failure during librarian maintenance."""


__all__ = ["LibrarianProviderRequestFailure"]
