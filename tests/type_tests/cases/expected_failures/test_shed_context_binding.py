"""Shed factories reject contexts for a different tool."""

from roboz.runtime import EventPipe
from roboz.shed.tools import SleepContext, purge_files

# Expected: reportArgumentType; purge_files requires PurgeFilesContext.
purge_files(SleepContext(pipe=EventPipe(), seconds=0))
