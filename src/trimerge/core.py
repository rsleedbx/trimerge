"""Three-way merge of Python dicts and lists.

Algorithm
---------
Given three versions of the same data structure — ``base`` (the last known
shared state), ``ours`` (the user's edits), and ``theirs`` (the incoming
update) — produce a merged result and a list of :class:`Conflict` objects for
paths where both ``ours`` and ``theirs`` diverged from ``base``.

Four cases at every node
~~~~~~~~~~~~~~~~~~~~~~~~
1. ``ours == theirs`` — both agree; return either (no conflict).
2. ``ours == base``  — user did not change it; take ``theirs`` silently.
3. ``theirs == base`` — incoming did not change it; keep ``ours`` silently.
4. All three differ — recurse into dicts/named-lists; raise a
   :class:`~trimerge.conflicts.Conflict` for scalars.

Named lists
~~~~~~~~~~~
Lists whose items are dicts containing a ``name`` key are merged by matching
items on that key (like a primary key join), enabling column-level or
table-level merges without index-position sensitivity.  All other lists are
treated as opaque values: if all three versions differ the whole list is a
conflict.
"""

from __future__ import annotations

import copy
from typing import Any

from .conflicts import DELETED, Conflict


def merge(
    base: Any,
    ours: Any,
    theirs: Any,
) -> tuple[Any, list[Conflict]]:
    """Three-way merge of *base*, *ours*, and *theirs*.

    Parameters
    ----------
    base:
        The last known shared state (e.g. the shadow / pristine copy written
        by the previous ``collect`` run).
    ours:
        The user-edited version.
    theirs:
        The incoming update (e.g. the new ``collect`` output).

    Returns
    -------
    merged:
        The reconciled structure.  At unresolved conflicts ``ours`` wins.
    conflicts:
        List of :class:`~trimerge.conflicts.Conflict` objects — one per path
        where both ``ours`` and ``theirs`` changed the same value.
    """
    conflicts: list[Conflict] = []
    result = _merge(base, ours, theirs, "root", conflicts)
    return result, conflicts


# ── internal helpers ──────────────────────────────────────────────────────────

_MISSING: object = object()  # sentinel: key absent from a particular version


def _merge(base: Any, ours: Any, theirs: Any, path: str, conflicts: list[Conflict]) -> Any:
    # Fast path: both sides agree — nothing to do.
    if ours == theirs:
        return copy.deepcopy(ours)

    # Only collect (theirs) changed — apply silently.
    if ours == base:
        return copy.deepcopy(theirs)

    # Only user (ours) changed — keep silently.
    if theirs == base:
        return copy.deepcopy(ours)

    # All three differ — recurse into containers.
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        return _merge_dict(base, ours, theirs, path, conflicts)

    if isinstance(base, list) and isinstance(ours, list) and isinstance(theirs, list):
        return _merge_list(base, ours, theirs, path, conflicts)

    # Scalar or type mismatch — genuine conflict; ours wins by default.
    conflicts.append(Conflict(path=path, base_val=base, ours_val=ours, theirs_val=theirs))
    return copy.deepcopy(ours)


def _merge_dict(
    base: dict, ours: dict, theirs: dict, path: str, conflicts: list[Conflict]
) -> dict:
    all_keys = set(base) | set(ours) | set(theirs)
    result: dict = {}

    for key in sorted(all_keys):  # sorted for deterministic output
        key_path = f"{path}['{key}']"
        b = base.get(key, _MISSING)
        o = ours.get(key, _MISSING)
        t = theirs.get(key, _MISSING)

        val = _merge_keyed(b, o, t, key_path, conflicts)
        if val is not _MISSING:
            result[key] = val

    return result


def _merge_keyed(
    b: Any, o: Any, t: Any, path: str, conflicts: list[Conflict]
) -> Any:
    """Merge one key across base/ours/theirs; return *_MISSING* if deleted."""
    b_absent = b is _MISSING
    o_absent = o is _MISSING
    t_absent = t is _MISSING

    # Both sides deleted the key.
    if o_absent and t_absent:
        return _MISSING

    # Key exists only in theirs (collect added a new key user never saw).
    if b_absent and o_absent:
        return copy.deepcopy(t)

    # Key exists only in ours (user added a key collect never saw).
    if b_absent and t_absent:
        return copy.deepcopy(o)

    # Both sides added the same key independently (not in base).
    if b_absent:
        if o == t:
            return copy.deepcopy(o)
        conflicts.append(Conflict(path=path, base_val=None, ours_val=o, theirs_val=t))
        return copy.deepcopy(o)  # ours wins

    # User deleted; incoming still has it.
    if o_absent:
        if t == b:
            return _MISSING  # incoming didn't change — respect deletion
        # Incoming changed the value the user deleted — conflict; keep deleted.
        conflicts.append(Conflict(path=path, base_val=b, ours_val=DELETED, theirs_val=t))
        return _MISSING

    # Incoming deleted; user still has it.
    if t_absent:
        if o == b:
            return _MISSING  # user didn't change — respect incoming deletion
        # User changed the value that incoming deleted — conflict; keep ours.
        conflicts.append(Conflict(path=path, base_val=b, ours_val=o, theirs_val=DELETED))
        return copy.deepcopy(o)

    # All three present — normal recursive merge.
    return _merge(b, o, t, path, conflicts)


# ── list merging ──────────────────────────────────────────────────────────────


def _is_named_list(lst: list) -> bool:
    """Return True if every item is a dict containing a ``name`` key."""
    return bool(lst) and all(isinstance(item, dict) and "name" in item for item in lst)


def _merge_list(
    base: list, ours: list, theirs: list, path: str, conflicts: list[Conflict]
) -> list:
    """Dispatch to named-list or opaque-list merge."""
    if _is_named_list(base) or _is_named_list(ours) or _is_named_list(theirs):
        return _merge_named_list(base, ours, theirs, path, conflicts)

    # Opaque list — already handled the easy cases in _merge; this is a conflict.
    conflicts.append(Conflict(path=path, base_val=base, ours_val=ours, theirs_val=theirs))
    return copy.deepcopy(ours)


def _merge_named_list(
    base: list, ours: list, theirs: list, path: str, conflicts: list[Conflict]
) -> list:
    """Merge lists of dicts by matching items on the ``name`` key.

    Ordering follows ``ours`` first, then any new items added by ``theirs``.
    """
    base_map   = {item["name"]: item for item in base   if isinstance(item, dict) and "name" in item}
    ours_map   = {item["name"]: item for item in ours   if isinstance(item, dict) and "name" in item}
    theirs_map = {item["name"]: item for item in theirs if isinstance(item, dict) and "name" in item}

    # Preserve ours ordering; append theirs-only items at the end.
    seen: set[str] = set()
    names: list[str] = []
    for item in ours:
        if isinstance(item, dict) and "name" in item:
            names.append(item["name"])
            seen.add(item["name"])
    for item in theirs:
        if isinstance(item, dict) and "name" in item and item["name"] not in seen:
            names.append(item["name"])

    result = []
    for name in names:
        item_path = f"{path}[name='{name}']"
        b = base_map.get(name, _MISSING)
        o = ours_map.get(name, _MISSING)
        t = theirs_map.get(name, _MISSING)

        val = _merge_keyed(b, o, t, item_path, conflicts)
        if val is not _MISSING:
            result.append(val)

    return result
