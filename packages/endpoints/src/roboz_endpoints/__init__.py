"""Named lazy model collections from the endpoint inventory."""

from roboz_endpoints.inventory import CATALOGS as _catalogs

globals().update(_catalogs)
__all__ = list(_catalogs)
