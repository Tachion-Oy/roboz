"""Command specifications for run_file_command."""

import re

from roboz.shed.models import Operation
from roboz.shed.tools.cli_commands.utilities.cmd_spec import ArgPattern, CmdSpec
from roboz.shed.tools.cli_commands.utilities.path_extractors import (
    cat_path_args,
    cp_path_args,
    diff_path_args,
    find_path_args,
    gio_trash_path_args,
    grep_path_args,
    head_path_args,
    ls_path_args,
    mkdir_path_args,
    mv_path_args,
    rg_path_args,
    tail_path_args,
    tee_path_args,
    touch_path_args,
    wc_path_args,
)

GREP = CmdSpec(name="grep", path_extractor=grep_path_args)
RG = CmdSpec(
    name="rg",
    path_extractor=rg_path_args,
    forbidden_patterns=[
        ArgPattern(pattern=re.compile(r"--pre"), position=None),
        ArgPattern(pattern=re.compile(r"--pre-glob"), position=None),
    ],
)
PWD = CmdSpec(name="pwd")
CAT = CmdSpec(name="cat", path_extractor=cat_path_args)
HEAD = CmdSpec(name="head", path_extractor=head_path_args)
TAIL = CmdSpec(name="tail", path_extractor=tail_path_args)
FIND = CmdSpec(
    name="find",
    path_extractor=find_path_args,
    forbidden_patterns=[
        ArgPattern(pattern=re.compile(r"-exec"), position=None),
        ArgPattern(pattern=re.compile(r"-execdir"), position=None),
        ArgPattern(pattern=re.compile(r"-ok"), position=None),
        ArgPattern(pattern=re.compile(r"-okdir"), position=None),
        ArgPattern(pattern=re.compile(r"-delete"), position=None),
        ArgPattern(pattern=re.compile(r"-fprint"), position=None),
        ArgPattern(pattern=re.compile(r"-fprint0"), position=None),
        ArgPattern(pattern=re.compile(r"-fprintf"), position=None),
        ArgPattern(pattern=re.compile(r"-fls"), position=None),
    ],
)
LS = CmdSpec(name="ls", path_extractor=ls_path_args)
WC = CmdSpec(name="wc", path_extractor=wc_path_args)
DIFF = CmdSpec(name="diff", path_extractor=diff_path_args)

TEE = CmdSpec(
    name="tee",
    operation=Operation.CREATE,
    path_extractor=tee_path_args,
    hint='Without -a, overwrites. Use argv=["-a", "...path..."] to append.',
)
TOUCH = CmdSpec(
    name="touch",
    operation=Operation.CREATE,
    path_extractor=touch_path_args,
)
MKDIR = CmdSpec(
    name="mkdir",
    operation=Operation.CREATE,
    path_extractor=mkdir_path_args,
)
MV = CmdSpec(
    name="mv",
    operation=Operation.CREATE,
    source_operation=Operation.DELETE,
    path_extractor=mv_path_args,
    allowed_patterns=[
        ArgPattern(pattern=re.compile(r"--"), position=None),
        ArgPattern(pattern=re.compile(r"-t"), position=None),
        ArgPattern(pattern=re.compile(r"--target-directory"), position=None),
        ArgPattern(pattern=re.compile(r"-T"), position=None),
        ArgPattern(pattern=re.compile(r"--no-target-directory"), position=None),
        ArgPattern(pattern=re.compile(r"-v"), position=None),
        ArgPattern(pattern=re.compile(r"--verbose"), position=None),
        ArgPattern(pattern=re.compile(r"--strip-trailing-slashes"), position=None),
    ],
    hint=(
        "Move/rename paths: sources require DELETE, destination requires CREATE. "
        "Overwriting an existing destination also requires READ and DELETE there."
    ),
)

CP = CmdSpec(
    name="cp",
    operation=Operation.CREATE,
    source_operation=Operation.READ,
    path_extractor=cp_path_args,
    allowed_patterns=[
        ArgPattern(pattern=re.compile(r"--"), position=None),
        ArgPattern(pattern=re.compile(r"-r"), position=None),
        ArgPattern(pattern=re.compile(r"-R"), position=None),
        ArgPattern(pattern=re.compile(r"--recursive"), position=None),
        ArgPattern(pattern=re.compile(r"-t"), position=None),
        ArgPattern(pattern=re.compile(r"--target-directory"), position=None),
        ArgPattern(pattern=re.compile(r"-T"), position=None),
        ArgPattern(pattern=re.compile(r"--no-target-directory"), position=None),
        ArgPattern(pattern=re.compile(r"-v"), position=None),
        ArgPattern(pattern=re.compile(r"--verbose"), position=None),
        ArgPattern(pattern=re.compile(r"--strip-trailing-slashes"), position=None),
    ],
    hint=(
        "Copy files or directories: sources require READ, destination requires CREATE. "
        "Use -r/-R/--recursive for directories."
    ),
)

GIO = CmdSpec(
    name="gio",
    operation=Operation.DELETE,
    path_extractor=gio_trash_path_args,
    allowed_patterns=[
        ArgPattern(pattern=re.compile(r"trash"), position=0),
        ArgPattern(pattern=re.compile(r"trash"), position=None),
    ],
    hint="Use argv starting with trash; moves files to trash (safer than rm).",
)

FILE_COMMANDS_READ = (GREP, RG, PWD, CAT, HEAD, TAIL, FIND, LS, WC, DIFF)
FILE_COMMANDS_WRITE = (TEE, TOUCH, MKDIR, MV, CP)
FILE_COMMANDS_DELETE = (GIO,)
