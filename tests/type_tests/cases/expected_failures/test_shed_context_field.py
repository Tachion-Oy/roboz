"""Context constructor fields preserve their concrete types."""

from roboshed.tools import PurgeFilesContext

# Expected: reportArgumentType; folders must contain Path objects.
PurgeFilesContext(folders=["data"], pattern="*.md", max_files=5)
