"""Concrete context constructor fields are checked."""

from roboz.dependencies import ExecutableDependency
from tests.type_tests.fixtures.primitive_contexts import ProgramContext

# Expected: reportArgumentType; prefix must be str.
ProgramContext(executable=ExecutableDependency("python"), prefix=123)
