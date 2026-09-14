# Public imports

RoboZ keeps its most common authoring primitives at the package root and groups
the rest of its API by domain:

```python
from roboz import Agent, Factory, Skill, Tool, factory, tool
from roboz.agent import run_subagent
from roboz.llm import LLMEndpoint
from roboz.models import Empty, Message, Str
from roboz.tools import stop
from roboz.tooling import HasExternalDependencies
```

`import roboz as rz` also exposes each domain for interactive discovery and
completion, so `rz.models.Str`, `rz.llm.LLMEndpoint`, and `rz.tools.stop` are
supported. Domains load when first accessed; importing `roboz` alone does not
initialize the model, agent, or runtime packages.

## Migration from flat imports

The following names are no longer exported directly by `roboz`:

| Previous root names | Import from |
| --- | --- |
| `AgentBaseModel`, `All`, `Empty`, `HashMaps`, `Int`, `Invoke`, `Location`, `LocationStr`, `Message`, `Role`, `Stop`, `StopLocation`, `Str`, `Strs` | `roboz.models` |
| `BackgroundAgentStatus`, `prompt_agent`, `run_background_agent`, `run_subagent` | `roboz.agent` |
| `PromptUser`, `message_user`, `prompt_user`, `prompt_user_at_start`, `stop`, `stop_after` | `roboz.tools` |
| `HasExternalDependencies`, `Materializable` | `roboz.tooling` |

For example, replace:

```python
from roboz import Empty, Message, Str, factory, stop
```

with:

```python
from roboz import factory
from roboz.models import Empty, Message, Str
from roboz.tools import stop
```

`roboz.tooling` exports `Tool`, `Factory`, `tool`, and `factory` for callers that
prefer a fully domain-qualified authoring API. Exception classes remain in
`roboz.exceptions`.
