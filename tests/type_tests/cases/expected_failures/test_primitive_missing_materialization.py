"""Explicit materialization declarations require an implementation."""

from roboz import Materializable


class Unfinished(Materializable):
    pass


# Expected: reportAbstractUsage for Materializable.materialize.
Unfinished()
