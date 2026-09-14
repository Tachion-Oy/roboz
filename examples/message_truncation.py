import json

from roboz.llm import get_truncated_messages_for_context
from roboz.models import NO_MESSAGE, NO_TRUNCATION, Message, Role, Severity, Truncation

TRACEBACK_LIFECYCLE = [
    Truncation(threshold=0, severity=Severity.LIGHT),
    Truncation(threshold=3, severity=Severity.STUB),
    Truncation(threshold=6, severity=Severity.REMOVE),
]

traceback_message = Message(
    role=Role.USER,
    content=json.dumps(
        {
            "caller": "run_job",
            "value": "Traceback: the illustrative job failed",
        }
    ),
    truncation=TRACEBACK_LIFECYCLE,
)


def visibility_after(newer_count: int) -> str:
    """Describe the traceback's model-visible state after newer messages arrive."""
    newer_messages = [
        Message(
            role=Role.USER,
            content=f"newer message {index}",
            truncation=NO_TRUNCATION,
        )
        for index in range(newer_count)
    ]
    visible = get_truncated_messages_for_context(
        [traceback_message, *newer_messages]
    )
    projected = [message for message in visible if "run_job" in message.content]
    if not projected:
        return "removed"
    if "<message truncated>" in projected[0].content:
        return "stubbed"
    return "visible"


for distance in (0, 3, 6):
    print(f"distance {distance}: {visibility_after(distance)}")

internal_message = Message(
    role=Role.USER,
    content="retained operational detail",
    truncation=NO_MESSAGE,
)
assert get_truncated_messages_for_context([internal_message]) == []
assert "Traceback" in traceback_message.content
print("NO_MESSAGE: retained outside model context")
