"""Prompts for file editing (apply_patch; string replace under guards)."""

_BODY: str = """## File editing (`__AP__`)

This workflow assumes you already know **`__RC__`** from the **`__ST__`** skill: use it to **read and search** the tree before editing. This tool edits **one file at a time**, using **literal find/replace** (exact substring match) with the same path guards as the CLI tools.

### When to use this vs full-file rewrite

Use this decision rule:

1. **Small files (roughly <200-300 lines):** prefer rewriting the full file when many parts need to change, either with **`__RC__`** or **`__AP__`**.
2. **Find/replace edits (single region or repeated exact text):** use **`__AP__`**.
3. **Large files or risky targeted edits:** use **`__AP__`** with strong context.

### What you send (structured JSON)

The tool takes a **single JSON object** with:

- **`path`**: one path to a regular file target; it may be **relative** (resolved under the tool base) or **absolute** (same guard rules apply). Globs are not allowed.
- **`old_string`**: the **exact** text to find in the file. Not a regex; whitespace, tabs, and newlines must match the file byte-for-byte in that region.
- **`new_string`**: the replacement text (may be empty to delete the matched substring).
- **`replace_all`**: boolean. If **`false`**, **`old_string`** must appear **exactly once** in the file. If **`true`**, **every** occurrence is replaced; at least one occurrence is required.

Special mode:

- If **`old_string`** is empty (`""`), the tool does a **full-file rewrite** to **`new_string`** instead of find/replace, **`replace_all`** is irrelevant.
- In rewrite mode, if the file does not exist, it is **created**.

There is **no** shell, **no** heredoc, and **no** unified diff: you pass **`path` + strings** only.

### JSON examples (actual payload shape)

Single replacement (exactly one match expected):

```json
{
  "path": "src/app.py",
  "old_string": "return old_value\\n",
  "new_string": "return new_value\\n",
  "replace_all": false
}
```

Replace all repeated occurrences:

```json
{
  "path": "src/constants.ts",
  "old_string": "API_V1",
  "new_string": "API_V2",
  "replace_all": true
}
```

Context-based replacement for a specific block:

```json
{
  "path": "src/service.py",
  "old_string": "def compute(x):\\n    return x + 1\\n",
  "new_string": "def compute(x):\\n    return x + 2\\n",
  "replace_all": false
}
```

Full-file rewrite (also creates the file if missing):

```json
{
  "path": "src/new_or_existing.txt",
  "old_string": "",
  "new_string": "entire file contents\\n",
  "replace_all": false
}
```

### Matching rules

1. **Exact substring:** When **`old_string`** is non-empty, the file is searched for it as a literal slice. If the model omits a trailing newline that exists in the file (or the opposite), the call **fails** with "not found" or "matched N times". Re-read the file and copy the exact span.
2. **Rewrite mode:** When **`old_string`** is empty, matching rules are skipped and the file becomes exactly **`new_string`**.
3. **Uniqueness when `replace_all` is false:** For non-empty **`old_string`**, if it appears zero times or more than once, the tool **fails**. Add surrounding lines to **`old_string`** (and **`new_string`**) until the match is unique.
4. **Multiple edits:** Use **`replace_all`: true** when you intend to change every occurrence of the same snippet; use **`false`** for a single targeted change.
5. **One file per invocation.** To touch two files, use two **`__AP__`** calls.

### Limitations

1. **Single-file scope** — each call targets one path only.
2. **Path permissions:** the same **allow / deny / ask** rules as **`__RC__`** writes apply. Tool input paths may be relative or absolute, and permission rules may also be relative or absolute patterns. If no rule permits the target, the operation is blocked **before** any edit.
3. **Failed find/replace edits:** if non-empty matching rules are not satisfied, the file should remain unchanged; verify with **`cat`** / **`rg`** when unsure.
4. **No rename/move operation:** use **`__RC__`** with `mv` for file or directory moves; this tool only edits file contents.

### Working with `__RC__` (explore, then edit)

Use **`__ST__`** for **targeted reads** so **`old_string`** matches reality:

- **`cat -n path`** — line numbers; copy only real file content (not the `cat -n` prefix column).
- **`rg -n`** or **`grep -n`** — find matches and line context.
- **`head` / `tail`** — slices; combine with **`wc -l`** if you need length.

### Find/replace guidance

- For **single replacement**, make `old_string` unique by including enough nearby context.
- For **replace-all**, use a deliberately narrow token/string so you do not unintentionally change unrelated text.
- If a single-replace call reports multiple matches, expand `old_string` to include adjacent lines.

**Workflow:** read the relevant region, build a **minimal but unique** **`old_string`** (often 1-3 lines of context), set **`new_string`**, choose **`replace_all`**, call **`__AP__`**, then read again to confirm."""


def _expand_body(
    *,
    apply_patch_tool_name: str,
    run_file_command_tool_name: str,
    cli_commands_skill_name: str,
) -> str:
    return (
        _BODY.replace("__AP__", apply_patch_tool_name)
        .replace("__RC__", run_file_command_tool_name)
        .replace("__ST__", cli_commands_skill_name)
    )


def build_description(
    *,
    cli_commands_skill_name: str,
    run_file_command_tool_name: str,
    apply_patch_tool_name: str,
) -> str:
    return (
        f"Applies precise, single-file code and text edits using the `{apply_patch_tool_name}` tool "
        "(literal find/replace under path guards). Explains structured input (`path`, `old_string`, "
        f"`new_string`, `replace_all`), matching rules, and how to combine with `{run_file_command_tool_name}` "
        f"exploration from the `{cli_commands_skill_name}` skill. Supports exact substring replacement and "
        'full-file rewrite mode (`old_string: ""`) for overwrite/create workflows.'
    )


def build_instructions(
    *,
    cli_commands_skill_name: str,
    run_file_command_tool_name: str,
    apply_patch_tool_name: str,
) -> str:
    return _expand_body(
        apply_patch_tool_name=apply_patch_tool_name,
        run_file_command_tool_name=run_file_command_tool_name,
        cli_commands_skill_name=cli_commands_skill_name,
    )
