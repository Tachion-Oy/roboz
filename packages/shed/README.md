# roboshed

Roboshed provides reusable agent components built on [RoboZ](https://pypi.org/project/roboz/),
including file tools, conversation and memory maintenance, and assembled agent
setups. It builds on RoboZ's general primitives with implementations that
applications can adopt or extend.

Use individual tools in your own agents, add capabilities that bundle tools and
their instructions, or start from an existing agent definition. Deployment
recipes combine those definitions into cooperating agents with shared project
configuration. Your application supplies the models and controls execution.

Requires Python 3.13 or newer.

```bash
uv add roboshed
```

## Where to look

- [roboshed.agents](https://github.com/Tachion-Oy/roboz/tree/main/packages/shed/src/roboshed/agents) contains agent definitions with their
  prompts and capabilities; [roboshed.deployments](https://github.com/Tachion-Oy/roboz/tree/main/packages/shed/src/roboshed/deployments)
  assembles them with project context and persistence.
- [roboshed.capabilities](https://github.com/Tachion-Oy/roboz/blob/main/packages/shed/src/roboshed/capabilities.py) bundles behavior for adding
  to agents, binding tools to the agent's configuration when built.
- [roboshed.tools](https://github.com/Tachion-Oy/roboz/tree/main/packages/shed/src/roboshed/tools) contains individual tool implementations;
  [roboshed.skills](https://github.com/Tachion-Oy/roboz/tree/main/packages/shed/src/roboshed/skills) provides instructions for agents using
  those tools.
- [roboshed.sandbox](https://github.com/Tachion-Oy/roboz/blob/main/packages/shed/src/roboshed/sandbox.py) defines filesystem scopes and
  permission policies for file tools.
- [roboshed.dependency_health](https://github.com/Tachion-Oy/roboz/blob/main/packages/shed/src/roboshed/dependency_health.py) checks external
  resources, such as model services and executables, and records their availability.

Shed's permission policies guard Shed tools. They are not an operating-system
sandbox.

## License

Licensed under the [Apache License 2.0](https://github.com/Tachion-Oy/roboz/blob/main/packages/shed/LICENSE).
