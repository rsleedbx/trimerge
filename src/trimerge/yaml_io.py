"""YAML file merge with comment preservation.

Uses ``ruamel.yaml`` round-trip loading so user comments, key ordering, and
inline flow-style values survive the merge.

Comment-preservation strategy
------------------------------
The merge result is constructed by starting from ``ours`` (the user-edited
``CommentedMap``) and applying changes from ``theirs`` (the incoming update)
for paths where the user made no edits.  Because we mutate the ``ours``
structure in-place, all existing comments on unchanged keys survive.  New keys
contributed by ``theirs`` are inserted without comments — acceptable given
that the source of truth for those values is machine-generated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .conflicts import Conflict
from .core import merge

_RUAMEL_MISSING = (
    "trimerge's YAML support requires ruamel.yaml.\n"
    "Install it with:  pip install 'trimerge[yaml]'  or  pip install ruamel.yaml"
)


def _require_ruamel():
    try:
        import ruamel.yaml  # noqa: F401
    except ImportError as exc:
        raise ImportError(_RUAMEL_MISSING) from exc


def _yaml_instance():
    _require_ruamel()
    from ruamel.yaml import YAML
    y = YAML()
    y.default_flow_style = False
    y.preserve_quotes = True
    y.width = 120
    return y


def _load(path: str | Path) -> Any:
    y = _yaml_instance()
    with open(path, encoding="utf-8") as f:
        return y.load(f)


def _to_plain(obj: Any) -> Any:
    """Recursively convert ruamel CommentedMap/Seq to plain dicts/lists."""
    try:
        from ruamel.yaml.comments import CommentedMap, CommentedSeq
    except ImportError:
        return obj
    if isinstance(obj, CommentedMap):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, CommentedSeq):
        return [_to_plain(v) for v in obj]
    return obj


def _apply_plain_to_commented(target: Any, source: Any) -> Any:
    """Update *target* (ruamel commented object) to reflect *source* (plain dict/list).

    - Keys present in *source* but absent in *target* are added (no comment).
    - Keys present in *target* but absent in *source* are removed.
    - Scalar values that changed are updated; existing comments are preserved.
    - Nested dicts are recursed into to maximise comment retention.
    """
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if isinstance(target, CommentedMap) and isinstance(source, dict):
        # Remove keys that were deleted from the merge result.
        for key in list(target.keys()):
            if key not in source:
                del target[key]
        # Add / update keys.
        for key, val in source.items():
            if key not in target:
                target[key] = _to_commented(val)
            else:
                target[key] = _apply_plain_to_commented(target[key], val)
        return target

    if isinstance(target, CommentedSeq) and isinstance(source, list):
        # For lists: replace contents, retaining the CommentedSeq shell.
        target.clear()
        for item in source:
            target.append(_to_commented(item))
        return target

    # Scalar or type mismatch — replace outright.
    return _to_commented(source)


def _to_commented(obj: Any) -> Any:
    """Convert a plain Python object to a ruamel commented equivalent."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    if isinstance(obj, dict):
        cm = CommentedMap()
        for k, v in obj.items():
            cm[k] = _to_commented(v)
        return cm
    if isinstance(obj, list):
        cs = CommentedSeq()
        for item in obj:
            cs.append(_to_commented(item))
        return cs
    return obj


def merge_files(
    base_path: str | Path,
    ours_path: str | Path,
    theirs_path: str | Path,
) -> tuple[Any, list[Conflict]]:
    """Three-way merge of three YAML files.

    Parameters
    ----------
    base_path:
        Path to the shadow / pristine copy (last machine-written state).
    ours_path:
        Path to the user-edited file.
    theirs_path:
        Path to the new incoming version (e.g. fresh ``collect`` output).

    Returns
    -------
    merged:
        A ``ruamel.yaml`` ``CommentedMap`` with user comments preserved for
        unchanged keys.
    conflicts:
        List of :class:`~trimerge.conflicts.Conflict` objects.
    """
    base_commented   = _load(base_path)
    ours_commented   = _load(ours_path)
    theirs_commented = _load(theirs_path)

    base_plain   = _to_plain(base_commented)
    ours_plain   = _to_plain(ours_commented)
    theirs_plain = _to_plain(theirs_commented)

    merged_plain, conflicts = merge(base_plain, ours_plain, theirs_plain)

    # Apply the plain merged result back onto the commented ours structure so
    # that user comments on unchanged keys are preserved.
    merged_commented = _apply_plain_to_commented(ours_commented, merged_plain)
    return merged_commented, conflicts


def save_merged(
    merged: Any,
    path: str | Path,
) -> None:
    """Write the merged result to *path* using ruamel.yaml (comment-preserving)."""
    y = _yaml_instance()
    with open(path, "w", encoding="utf-8") as f:
        y.dump(merged, f)
