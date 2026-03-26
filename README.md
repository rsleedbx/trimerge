# trimerge

Three-way merge of Python dicts and YAML files with conflict detection.

[![PyPI](https://img.shields.io/badge/pypi-coming%20soon-orange)](https://pypi.org/project/trimerge/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/trimerge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

```
pip install trimerge           # dict merge — no deps
pip install 'trimerge[yaml]'   # + YAML file merge with comment preservation
```

## The problem

You have a machine-generated YAML file (config, schema, stats). You let users edit it. Later you want to update it with a fresh machine-generated version — without stomping the user's edits.

The pattern is identical to git's three-way merge:

| shadow (base) | your file (ours) | new version (theirs) | Result |
|---|---|---|---|
| `x: 1` | `x: 1` | `x: 2` | auto-update to `2` — you never touched it |
| `x: 1` | `x: 9` | `x: 1` | keep `9` — you changed it, incoming didn't |
| `x: 1` | `x: 9` | `x: 2` | **conflict** — both changed it, surface to user |
| `x: 1` | `x: 1` | `x: 1` | no change |

## Quick start

### Dict merge (no dependencies)

```python
from trimerge import merge, Conflict

base   = {"a": 1, "b": 2}
ours   = {"a": 9, "b": 2}   # user changed "a"
theirs = {"a": 1, "b": 5}   # incoming changed "b"

merged, conflicts = merge(base, ours, theirs)
# merged    → {"a": 9, "b": 5}   both changes applied silently
# conflicts → []
```

### Conflict detection

```python
base   = {"null_fraction": 0.02}
ours   = {"null_fraction": 0.40}  # user tuned for stress test
theirs = {"null_fraction": 0.05}  # new measurement from source DB

merged, conflicts = merge(base, ours, theirs)
# conflicts[0].path      → "root['null_fraction']"
# conflicts[0].ours_val  → 0.40
# conflicts[0].theirs_val → 0.05
# merged (ours wins by default) → {"null_fraction": 0.40}
```

### YAML file merge with comment preservation

```python
from trimerge import merge_files, save_merged

# base_path   = shadow file written by the previous machine run
# ours_path   = user-edited file
# theirs_path = fresh machine-generated file

merged, conflicts = merge_files("shadow.yaml", "schema.yaml", "new_schema.yaml")

if not conflicts:
    save_merged(merged, "schema.yaml")   # update in place
```

User comments survive on keys that were not changed:

```yaml
# schema.yaml (ours)
null_fraction: 0.0
n_distinct: 100.0  # tuned for stress test — keep this!
```

After merge, the comment is preserved even if the machine updated `null_fraction`.

## Named lists

Lists whose items have a `name` key are merged by name rather than by index — perfect for YAML schemas with `tables:` and `columns:` lists:

```python
base = {"columns": [
    {"name": "id",     "null_fraction": 0.0},
    {"name": "status", "null_fraction": 0.0},
]}
ours = {"columns": [
    {"name": "id",     "null_fraction": 0.0},
    {"name": "status", "null_fraction": 0.40},  # user tuned status
]}
theirs = {"columns": [
    {"name": "id",     "null_fraction": 0.01},  # machine updated id
    {"name": "status", "null_fraction": 0.0},
]}

merged, conflicts = merge(base, ours, theirs)
# merged["columns"]["id"]["null_fraction"]     → 0.01  (machine update applied)
# merged["columns"]["status"]["null_fraction"] → 0.40  (user edit preserved)
# conflicts → []
```

## Conflict resolution

### Interactive (default)

```python
from trimerge import resolve_interactive

resolutions = resolve_interactive(conflicts)
# Prompts:
#   Conflict at root['null_fraction']
#     base:   0.02
#     yours:  0.40
#     theirs: 0.05
#   Keep [y]ours / [t]heirs / [s]kip?
```

### Non-interactive (CI/CD)

```python
from trimerge import write_conflict_file

if conflicts:
    write_conflict_file(conflicts, ".trimerge.conflicts.yaml")
    raise SystemExit(1)   # block pipeline until resolved
```

`.trimerge.conflicts.yaml`:
```yaml
conflicts:
  - path: root['null_fraction']
    base: 0.02
    yours: 0.4
    theirs: 0.05
```

## The shadow file pattern

The recommended pattern for tools that generate YAML and want safe re-generation:

```
schema.yaml          ← user edits this
.schema.shadow.yaml  ← machine writes this on every run (commit alongside schema.yaml)
```

On re-generate:
```python
merged, conflicts = merge_files(".schema.shadow.yaml", "schema.yaml", new_content_path)
if not conflicts:
    save_merged(merged, "schema.yaml")
    save_merged(new_content, ".schema.shadow.yaml")  # update shadow
else:
    write_conflict_file(conflicts, ".schema.conflicts.yaml")
    raise SystemExit(1)
```

## Who uses this pattern

The same "machine-generates, user edits, machine needs to re-generate safely" problem appears in:

- **Kubernetes / Helm** — `values.yaml` user overrides vs chart template updates
- **dbt** — `schema.yml` auto-generated column descriptions vs user annotations
- **Project scaffolding** (copier, cookiecutter) — updating a template without stomping user changes
- **Database migration tools** — schema files updated by collect runs vs user distribution tuning

## API reference

### `trimerge.merge(base, ours, theirs)`

Three-way merge of Python dicts/lists. Pure Python, no dependencies.

Returns `(merged: Any, conflicts: list[Conflict])`.

### `trimerge.merge_files(base_path, ours_path, theirs_path)`

Three-way merge of YAML files. Requires `ruamel.yaml` (`pip install 'trimerge[yaml]'`).

Returns `(merged: CommentedMap, conflicts: list[Conflict])`.

### `trimerge.save_merged(merged, path)`

Write a `CommentedMap` to a YAML file, preserving comments and key order.

### `trimerge.Conflict`

```python
@dataclass
class Conflict:
    path: str        # e.g. "root['tables'][name='orders']['null_fraction']"
    base_val: Any    # value in the shadow (base)
    ours_val: Any    # value in the user file (DELETED sentinel if key was deleted)
    theirs_val: Any  # value in the incoming file (DELETED sentinel if key was deleted)
```

### `trimerge.DELETED`

Sentinel object. `conflict.ours_val is DELETED` means the user deleted the key.
`conflict.theirs_val is DELETED` means the incoming version deleted the key.

## Installation

```bash
pip install trimerge              # core dict merge only (no deps)
pip install 'trimerge[yaml]'      # + YAML file merge (adds ruamel.yaml)
```

Requires Python 3.10+.
