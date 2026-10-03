"""Registry of supported tagged commands."""

from .commands.cat import CAT
from .commands.cp import CP
from .commands.head import HEAD
from .commands.mv import MV
from .commands.pwd import PWD
from .commands.tail import TAIL
from .commands.wc import WC

COMMANDS = {spec.name: spec for spec in (CP, MV, PWD, CAT, HEAD, TAIL, WC)}
