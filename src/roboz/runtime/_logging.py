import logging
import sys
from collections.abc import Mapping
from logging import Formatter, StreamHandler
from types import TracebackType
from typing import Final

type LogScalar = str | int | float | bool | None

LOG_DATA_ATTRIBUTE: Final[str] = "roboz_data"
_LOG_CALLER_STACKLEVEL: Final[int] = 2

# Format used by `reset_logging_config` only. Roboz never configures the root logger
# internally—doing so from library code would affect every importer.
#
# Some third-party LLM SDKs still mutate ``logging`` at import time. Roboz pulls those
# in for endpoint definitions, so that side effect can run even when a given endpoint
# is never used. Host applications may call ``reset_logging_config()`` after imports
# (or at ``main``) to impose a known layout—not something the library does for them.
# The thread name and millisecond timestamp are deliberate: Roboz runs external
# calls (and background agents) on separate threads, and cancellation/interrupt
# bugs are ordering bugs. Without ``threadName`` you cannot tell the run thread
# from the abandoned worker, and without millisecond precision you cannot tell
# which of two near-simultaneous events (signal set vs. worker finish) came first.
LOG_FORMAT: Final[str] = (
    "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(threadName)s | "
    "%(name)s | %(message)s"
)
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


def reset_logging_config(level: int = logging.INFO) -> None:
    """Opt-in helper: replace root handlers with Roboz's stderr formatter.

    **Roboz must not call this from its own library code.** It clears
    ``logging.root`` handlers and sets process-wide behavior, which would propagate
    to unrelated code in any project that depends on Agent.

    Typical uses: you want this console format for the whole process, or a dependency
    (often an LLM client imported for optional endpoints) has already attached handlers
    or changed levels and you need a clean, predictable root setup.

    Call only from *your* application or agent entrypoint—never as a side effect of
    constructing or running ``Agent`` inside the package.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    handler = StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root.addHandler(handler)


def log_with_data(
    logger: logging.Logger,
    level: int,
    message: str,
    data: Mapping[str, LogScalar] | None = None,
    *,
    exc_info: (
        tuple[type[BaseException], BaseException, TracebackType | None] | bool | None
    ) = None,
) -> None:
    """Log one readable message with optional scalar metadata for structured sinks."""
    extra: dict[str, object] = {}
    if data:
        extra[LOG_DATA_ATTRIBUTE] = dict(data)
    logger.log(
        level,
        message,
        extra=extra,
        exc_info=exc_info,
        stacklevel=_LOG_CALLER_STACKLEVEL,
    )


__all__ = [
    "LOG_DATA_ATTRIBUTE",
    "LOG_DATE_FORMAT",
    "LOG_FORMAT",
    "LogScalar",
    "log_with_data",
    "reset_logging_config",
]
