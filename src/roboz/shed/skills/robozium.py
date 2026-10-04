"""Robozium orientation and HUD output for project agents."""

# MIT License
#
# Copyright (c) 2026 Tachion Oy
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from roboz.skill import Skill

from roboz.shed.identifiers import ROBOZIUM_SKILL_NAME

robozium = Skill(
    name=ROBOZIUM_SKILL_NAME,
    description="Robozium orientation, project permissions, Librarian memory, and HUD output.",
    instructions="""## Robozium orientation

The host selects models, capabilities, sandbox layout, and project persistence
folders. Roboz and Roboshed supply reusable agent, tool, skill, and deployment
mechanisms. Describe available capabilities from the current tools and skills;
do not assume an unavailable tool exists.

Use the runtime-supplied file tool base, project slug, writable project directory,
read-only directory, and shared directory. The role name does not determine the
project directory. Use the supplied paths instead of guessing folder names.

## Sandbox permissions

The hub is the file-tool base. The standard Robozium layout is shown below;
the host can rename these folders, so use the actual paths in your project context.

```text
<hub>/
  projects/
    <current-project>/         Your writable project directory
      conversation_logs/       Runtime conversation history
      conversation_snapshots/  Librarian summaries
      persistent_memory/       Librarian memory
    <other-project>/           Readable, but not writable by this project
  workspace/                   Shared across projects; changes require approval
  readonly/                    Shared reference files; no changes allowed
```

| Location | Read | Create, modify, rename, or delete |
| --- | --- | --- |
| Current project and its descendants | Allowed | Allowed without a prompt |
| Other projects and their descendants | Allowed | Denied |
| Shared workspace and its descendants | Allowed | Ask the user for each operation requiring approval |
| Read-only reference area | Allowed | Denied |
| Other locations inside the hub | Allowed | Denied |
| Outside the hub | Denied | Denied |

Projects have separate writable areas, not private read boundaries. A folder
named `private` inside your project is still readable under this policy. Making
an in-hub path unreadable requires a host policy change. A deny-READ rule must
take precedence over the broad READ allow rule; the standard policy gives
allow rules precedence, so adding a conflicting deny rule alone is insufficient.
For example, with base `/hub` and project `testing`,
`projects/testing/private/secret.txt` is readable; `/tmp/private/secret.txt` is
outside the hub and denied. Relative paths start at the hub, not at your project.
Normalizing `.` or `..` does not change the rules for the resulting location.

The guard checks every required permission before asking for approval. An
existing-file write may require separate CREATE and DELETE approvals. Approval
applies to that operation; it does not grant standing permission for later calls
or override a policy denial. Approval questions go directly through the host UI.
Tool results include the question, the user's exact reply, and the decision.
Use that reply when deciding what to do next, including when the user declines
and gives further instructions. Do not infer that no prompt occurred merely
because a write succeeded or an earlier result is missing from your context.

File validation may stop a command before permission evaluation: a missing
parent or unsupported link is a preparation error, not a policy denial.
Content reads and writes reject symlinks and files with multiple hard links;
all names of a hard-linked file share that restriction. A symlink selected by a
glob rejects the whole read/search command before any content is read. Directory
searches with `grep -r` or `rg` skip descendant symlinks. `rm` can remove a terminal
link at its own pathname without reading or changing the target. These guards
apply to the file tools; they are not an operating-system sandbox.

## Librarian maintenance

The Librarian is a deterministic background pipeline. It snapshots conversations,
consolidates memory, applies retention limits, and waits between maintenance cycles.
After watched work becomes idle, it performs one complete final sweep without
sleeping and stops only after confirming that the project remains idle. The root
starts it through its background-start tool; the host owns cancellation and
shutdown. Maintenance is asynchronous, so a new conversation may not yet have
appeared in memory. Conversation logs are written by the runtime.

Do not duplicate that maintenance by writing your own session summaries or memory
files. Work in the conversation and let the Librarian maintain persistence. This
does not prevent creating documents the user has requested.

## HUD file links (`<file src="...">label</file>`)

When you create a file that the user should open from the HUD, link it using
an explicit file tag.

### Required link format

Use an inline `<file>` tag:

```md
<file src="relative/path.ext">Open the file</file>
```

### Path mapping

Map a file written under:

`{file_tool_base}/relative/path.ext`

to:

`src="relative/path.ext"` in `<file src="...">Label</file>`

### Authoring rules

1. Save user-openable outputs under the writable project root.
2. Derive `src` relative to the supplied file tool base. Use the actual project
   directory from the runtime context, not assumed folder names.
3. Use a clear human label (for example, `Open the file`).
4. Mention the format near the link when useful (PDF, DOCX, markdown, etc.).

### Never do this

1. Do not output absolute filesystem paths like `/home/...`.
2. Do not use `file://` URLs.
3. Do not emit traversal segments like `..` in `src`.
4. Do not include a leading slash in `src`.

## HUD message formatting (markdown + HTML)

HUD messages are rendered with GitHub-Flavored Markdown, and raw HTML is also
allowed. Use normal markdown freely.

### Supported markdown / HTML elements

These render as expected:

- Paragraphs and line breaks
- Headings `#`–`######`
- Bullet and numbered lists
- `**bold**`, `*italic*`, `~~strikethrough~~`
- Inline `` `code` `` and fenced code blocks
- Blockquotes (`> ...`)
- Horizontal rules (`---`)
- Links (external `http(s)` links open in a new tab)
- GFM tables (`| col | col |` with a `| --- | --- |` separator row)
- The `<file src="...">label</file>` tag described above

Raw HTML using the elements above is fine (for example, a hand-written
`<table>...</table>`); GFM markdown syntax is preferred where it exists.

### Not rendered (silently dropped)

For safety, anything outside the allowed set is removed along with its
content. In particular, do NOT rely on: `<script>`, `<style>`, `<img>`,
`<iframe>`, embedded media, or inline event handlers / `style` attributes.
Reference images and other media as file links instead.

## Answering questions

Keep explanations factual and concise. Distinguish host configuration from shared
mechanisms. Treat runtime paths and tool schemas as authoritative; ask for missing
context instead of inventing a location or capability.
""",
)
