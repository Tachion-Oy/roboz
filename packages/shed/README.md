# roboshed

Roboshed provides reusable agent components built on [RoboZ](../../README.md),
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

- [roboshed.agents](src/roboshed/agents) contains agent definitions with their
  prompts and capabilities; [roboshed.deployments](src/roboshed/deployments)
  assembles them with project context and persistence.
- [roboshed.capabilities](src/roboshed/capabilities.py) bundles behavior for adding
  to agents, binding tools to the agent's configuration when built.
- [roboshed.tools](src/roboshed/tools) contains individual tool implementations;
  [roboshed.skills](src/roboshed/skills) provides instructions for agents using
  those tools.
- [roboshed.sandbox](src/roboshed/sandbox.py) defines filesystem scopes and
  permission policies for file tools.
- [roboshed.dependency_health](src/roboshed/dependency_health.py) checks external
  resources, such as model services and executables, and records their availability.

Shed's permission policies guard Shed tools. They are not an operating-system
sandbox.

## License

Licensed under the [Apache License 2.0](LICENSE).
