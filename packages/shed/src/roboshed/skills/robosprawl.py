"""RoboSprawl orientation and HUD output, following PeffaHub and Robozium.

The original HUD guidance is retained with sandbox-relative path derivation.
"""

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

from roboshed.identifiers import ROBOSPRAWL_SKILL_NAME

robosprawl = Skill(
    name=ROBOSPRAWL_SKILL_NAME,
    description="RoboSprawl orientation, project permissions, Librarian memory, and HUD output.",
    instructions="""## RoboSprawl orientation

The host selects models, capabilities, sandbox layout, and project persistence
folders. Roboz and Roboshed supply reusable agent, tool, skill, and deployment
mechanisms. Describe available capabilities from the current tools and skills;
do not assume an unavailable tool exists.

Use the runtime-supplied file tool base, project slug, writable project directory,
read-only directory, and shared directory. The role name does not determine the
project directory. Use the supplied paths instead of guessing folder names.

## Sandbox permissions

- Reads are contained within the sandbox.
- Writes within the current project's directory are allowed.
- Writes to the shared directory require user confirmation.
- The read-only area and other projects are not writable.
- Paths outside the sandbox are denied.
- Confirmation can reject an allowed write; it cannot authorize a denied path.

## Librarian maintenance

The Librarian is a deterministic background pipeline. It snapshots conversations,
consolidates memory, applies retention limits, and waits between maintenance cycles.
The root starts it through its background-start tool; the host owns cancellation
and shutdown. Maintenance is asynchronous, so a new conversation may not yet have
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
