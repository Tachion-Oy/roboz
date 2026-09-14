from pathlib import Path

from roboz.models import Int, Str
from roboshed.models import GuardFileSingle, Operation

item = GuardFileSingle[Str](
    operation=Operation.READ, location=Path("/tmp"), value=Int(value=1)
)
