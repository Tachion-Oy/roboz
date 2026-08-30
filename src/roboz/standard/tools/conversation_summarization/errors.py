"""Errors shared by conversation summarization tools."""


class LibrarianProviderRequestFailure(RuntimeError):
    """Terminal provider request failure during librarian maintenance."""


__all__ = ["LibrarianProviderRequestFailure"]
