# roboz-shed

Reusable tools, skills, and a small assistant built on Roboz. Version `0.1.0a1`
is alpha; APIs may change before 1.0. Dependencies are Roboz and Pydantic only.

Includes guarded Unix file commands, Python patch editing, CLI/file/email
instructions, and provider-neutral email contracts and tools. It does not
install any model SDK, Proton, document SDK, web service, or backend framework.

```python
from pathlib import Path
from roboz.llm import MockLLMEndpoint
from roboz_shed.assistant import WorkspacePermissions, build_assistant

assistant = build_assistant(
    endpoint=MockLLMEndpoint([
        {"action": "stop", "rationale": "Complete", "value": "Hello"}
    ]),
    workspace=WorkspacePermissions.local(Path("./workspace")),
)
result, messages = assistant.invoke()
```

`build_assistant` accepts additional tools, skills, event sinks, initial messages,
and a system prompt. `tool_builders` receive the owning event pipe to bind
cancellation and events. Low-level `get_run_file_command`, `get_apply_patch`,
and `get_work_with_email` factories also work without this assistant.

The assistant supplies read commands and patch editing. The file factory can
also explicitly enable its write/delete command specifications. Permission
rules cover allow/deny/ask, configured precedence, overwrite checks, and resolved
paths; they are not an OS sandbox. Commands require their named Unix executables.

Generic guard models preserve input/payload types without integration-specific
unions. Use concrete generic parameters when decoding serialized guard results.

The installed demo requires only `cat` in mock mode:

```bash
python -m roboz_shed.demo --mock --workspace /tmp/roboz-demo-workspace --data-path /tmp/roboz-demo-data
```

It creates a uniquely named file and a saved conversation. Its real-provider
and email flags require the separately installed adapters. See the repository's
[installation guide](https://github.com/Tachion-Oy/roboz/blob/main/docs/addons.md)
for local wheel installation before these packages are published.
