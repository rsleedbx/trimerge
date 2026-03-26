"""trimerge — three-way merge of Python dicts and YAML files.

Quick start
-----------
**Dict merge (no I/O):**

>>> from trimerge import merge, Conflict, DELETED
>>> base   = {"a": 1, "b": 2}
>>> ours   = {"a": 9, "b": 2}   # user changed "a"
>>> theirs = {"a": 1, "b": 5}   # collect changed "b"
>>> merged, conflicts = merge(base, ours, theirs)
>>> merged
{'a': 9, 'b': 5}
>>> conflicts
[]

**YAML file merge (comment-preserving):**

>>> from trimerge import merge_files, save_merged
>>> merged, conflicts = merge_files("shadow.yaml", "edited.yaml", "new.yaml")
>>> save_merged(merged, "edited.yaml")

**Conflict handling:**

>>> base   = {"x": 1}
>>> ours   = {"x": 9}   # user changed x
>>> theirs = {"x": 2}   # collect also changed x
>>> merged, conflicts = merge(base, ours, theirs)
>>> len(conflicts)
1
>>> conflicts[0].path
"root['x']"
>>> # ours wins by default on unresolved conflicts
>>> merged["x"]
9
"""

from .conflicts import DELETED, Conflict, resolve_interactive, write_conflict_file
from .core import merge

try:
    from .yaml_io import merge_files, save_merged
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

__all__ = [
    "merge",
    "Conflict",
    "DELETED",
    "resolve_interactive",
    "write_conflict_file",
]

if _YAML_AVAILABLE:
    __all__ += ["merge_files", "save_merged"]

__version__ = "0.1.0"
