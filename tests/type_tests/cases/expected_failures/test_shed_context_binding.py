"""Shed factories reject contexts for a different tool."""

from roboshed.tools import SleepBetweenRunsContext, purge_files

# Expected: reportArgumentType; purge_files requires PurgeFilesContext.
purge_files(SleepBetweenRunsContext(seconds=0))
