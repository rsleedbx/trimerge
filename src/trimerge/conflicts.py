"""Conflict representation and resolution for three-way merges."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from ruamel.yaml import YAML as _YAML
except ImportError:
    _YAML = None  # type: ignore[assignment,misc]


DELETED: object = object()
"""Sentinel indicating a key was deleted in one of the versions.

When ``ours_val is DELETED``, the user deleted the key.
When ``theirs_val is DELETED``, the incoming version deleted the key.
"""

_DELETED_LABEL = "<deleted>"


def _display(val: Any) -> Any:
    return _DELETED_LABEL if val is DELETED else val


@dataclass
class Conflict:
    """A field where both the user and the incoming version diverged from the base."""

    path: str
    base_val: Any
    ours_val: Any
    theirs_val: Any

    def __str__(self) -> str:
        return (
            f"Conflict at {self.path}\n"
            f"  base:    {_display(self.base_val)}\n"
            f"  yours:   {_display(self.ours_val)}\n"
            f"  theirs:  {_display(self.theirs_val)}"
        )


def resolve_interactive(conflicts: list[Conflict]) -> dict[str, Any]:
    """Prompt the user to resolve each conflict interactively.

    Returns a ``{path: resolved_value}`` mapping for all conflicts the user
    resolved.  Skipped conflicts are not included — callers keep ``ours_val``
    for those.
    """
    resolutions: dict[str, Any] = {}
    for conflict in conflicts:
        print(f"\n{conflict}")
        while True:
            choice = input("  Keep [y]ours / [t]heirs / [s]kip? ").strip().lower()
            if choice in ("y", "yours"):
                resolutions[conflict.path] = conflict.ours_val
                break
            elif choice in ("t", "theirs"):
                resolutions[conflict.path] = conflict.theirs_val
                break
            elif choice in ("s", "skip"):
                break
            else:
                print("  Please enter y, t, or s.")
    return resolutions


def write_conflict_file(conflicts: list[Conflict], path: str | Path) -> None:
    """Write all conflicts to a YAML file for offline review.

    Suitable for CI/CD pipelines where interactive resolution is not possible.
    Exit non-zero after writing to signal that manual resolution is needed.
    """
    if not conflicts:
        return

    records = [
        {
            "path": c.path,
            "base":   _display(c.base_val),
            "yours":  _display(c.ours_val),
            "theirs": _display(c.theirs_val),
        }
        for c in conflicts
    ]

    if _YAML is not None:
        y = _YAML()
        y.default_flow_style = False
        with open(path, "w", encoding="utf-8") as f:
            y.dump({"conflicts": records}, f)
    else:
        import yaml as _pyyaml  # fallback to PyYAML
        with open(path, "w", encoding="utf-8") as f:
            _pyyaml.dump({"conflicts": records}, f, default_flow_style=False)
