"""Registry of supported tagged commands."""

from .commands.cat import CAT
from .commands.cp import CP
from .commands.diff import DIFF
from .commands.find import FIND
from .commands.gio import GIO
from .commands.grep import GREP
from .commands.head import HEAD
from .commands.ls import LS
from .commands.mkdir import MKDIR
from .commands.mv import MV
from .commands.pwd import PWD
from .commands.rg import RG
from .commands.rm import RM
from .commands.tail import TAIL
from .commands.tee import TEE
from .commands.touch import TOUCH
from .commands.wc import WC

COMMANDS = {
    spec.name: spec
    for spec in (CP, MV, PWD, CAT, HEAD, TAIL, WC, TEE, TOUCH, MKDIR, GREP, RG, LS, FIND, DIFF, GIO, RM)
}
