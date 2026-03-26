"""Tests for trimerge.core — pure dict/list three-way merge."""

from __future__ import annotations

import copy

import pytest

from trimerge import DELETED, Conflict, merge


# ── helpers ───────────────────────────────────────────────────────────────────


def conflict_paths(conflicts: list[Conflict]) -> list[str]:
    return [c.path for c in conflicts]


# ── Case 1: all three identical — no-op ───────────────────────────────────────


class TestNoOp:
    def test_identical_scalars(self):
        merged, conflicts = merge(42, 42, 42)
        assert merged == 42
        assert conflicts == []

    def test_identical_dicts(self):
        d = {"a": 1, "b": {"c": 2}}
        merged, conflicts = merge(d, copy.deepcopy(d), copy.deepcopy(d))
        assert merged == d
        assert conflicts == []

    def test_identical_named_list(self):
        lst = [{"name": "col1", "type": "int"}, {"name": "col2", "type": "text"}]
        merged, conflicts = merge(copy.deepcopy(lst), copy.deepcopy(lst), copy.deepcopy(lst))
        assert merged == lst
        assert conflicts == []


# ── Case 2: only user (ours) changed — preserve ours ─────────────────────────


class TestOursOnly:
    def test_scalar(self):
        merged, conflicts = merge(1, 9, 1)
        assert merged == 9
        assert conflicts == []

    def test_nested_key(self):
        base   = {"a": 1, "b": {"c": 2}}
        ours   = {"a": 1, "b": {"c": 99}}   # user changed b.c
        theirs = {"a": 1, "b": {"c": 2}}    # collect unchanged
        merged, conflicts = merge(base, ours, theirs)
        assert merged == {"a": 1, "b": {"c": 99}}
        assert conflicts == []

    def test_user_added_key(self):
        base   = {"a": 1}
        ours   = {"a": 1, "new_key": "user_value"}
        theirs = {"a": 1}
        merged, conflicts = merge(base, ours, theirs)
        assert merged["new_key"] == "user_value"
        assert conflicts == []

    def test_user_deleted_key(self):
        base   = {"a": 1, "b": 2}
        ours   = {"a": 1}            # user deleted "b"
        theirs = {"a": 1, "b": 2}   # collect unchanged
        merged, conflicts = merge(base, ours, theirs)
        assert "b" not in merged
        assert conflicts == []


# ── Case 3: only collect (theirs) changed — apply silently ───────────────────


class TestTheirsOnly:
    def test_scalar(self):
        merged, conflicts = merge(1, 1, 5)
        assert merged == 5
        assert conflicts == []

    def test_nested_key(self):
        base   = {"null_fraction": 0.02, "n_distinct": 100.0}
        ours   = {"null_fraction": 0.02, "n_distinct": 100.0}  # user unchanged
        theirs = {"null_fraction": 0.05, "n_distinct": 100.0}  # collect updated
        merged, conflicts = merge(base, ours, theirs)
        assert merged["null_fraction"] == 0.05
        assert conflicts == []

    def test_collect_added_key(self):
        base   = {"a": 1}
        ours   = {"a": 1}
        theirs = {"a": 1, "new_from_collect": 42}
        merged, conflicts = merge(base, ours, theirs)
        assert merged["new_from_collect"] == 42
        assert conflicts == []

    def test_collect_deleted_key(self):
        base   = {"a": 1, "b": 2}
        ours   = {"a": 1, "b": 2}   # user unchanged
        theirs = {"a": 1}            # collect deleted "b"
        merged, conflicts = merge(base, ours, theirs)
        assert "b" not in merged
        assert conflicts == []


# ── Case 4: both changed — conflict ──────────────────────────────────────────


class TestConflicts:
    def test_scalar_conflict(self):
        merged, conflicts = merge(1, 9, 2)
        assert len(conflicts) == 1
        assert conflicts[0].base_val == 1
        assert conflicts[0].ours_val == 9
        assert conflicts[0].theirs_val == 2
        assert merged == 9  # ours wins by default

    def test_nested_conflict(self):
        base   = {"null_fraction": 0.02}
        ours   = {"null_fraction": 0.40}  # user stress-tested
        theirs = {"null_fraction": 0.05}  # collect measured new value
        merged, conflicts = merge(base, ours, theirs)
        assert len(conflicts) == 1
        assert conflicts[0].ours_val == 0.40
        assert conflicts[0].theirs_val == 0.05
        assert merged["null_fraction"] == 0.40  # ours wins

    def test_no_conflict_for_other_keys(self):
        base   = {"x": 1, "y": 2}
        ours   = {"x": 9, "y": 2}   # user changed x only
        theirs = {"x": 1, "y": 5}   # collect changed y only
        merged, conflicts = merge(base, ours, theirs)
        assert merged == {"x": 9, "y": 5}
        assert conflicts == []

    def test_conflict_path_format(self):
        base   = {"tables": [{"name": "orders", "row_count_per_sf": 1000}]}
        ours   = {"tables": [{"name": "orders", "row_count_per_sf": 5000}]}
        theirs = {"tables": [{"name": "orders", "row_count_per_sf": 999}]}
        merged, conflicts = merge(base, ours, theirs)
        assert len(conflicts) == 1
        assert "orders" in conflicts[0].path
        assert "row_count_per_sf" in conflicts[0].path

    def test_user_deleted_collect_changed(self):
        """User deleted a key; collect also changed it → conflict; deletion wins."""
        base   = {"a": 1, "b": 2}
        ours   = {"a": 1}           # user deleted b
        theirs = {"a": 1, "b": 99}  # collect updated b
        merged, conflicts = merge(base, ours, theirs)
        assert "b" not in merged
        assert len(conflicts) == 1
        assert conflicts[0].ours_val is DELETED
        assert conflicts[0].theirs_val == 99

    def test_collect_deleted_user_changed(self):
        """Collect deleted a key; user also changed it → conflict; ours wins."""
        base   = {"a": 1, "b": 2}
        ours   = {"a": 1, "b": 99}  # user changed b
        theirs = {"a": 1}           # collect deleted b
        merged, conflicts = merge(base, ours, theirs)
        assert merged.get("b") == 99  # ours wins
        assert len(conflicts) == 1
        assert conflicts[0].theirs_val is DELETED


# ── Named-list merging ────────────────────────────────────────────────────────


class TestNamedList:
    def test_independent_column_changes(self):
        """User edits one column; collect updates a different column."""
        base = {
            "columns": [
                {"name": "id",   "null_fraction": 0.0},
                {"name": "status", "null_fraction": 0.0},
            ]
        }
        ours = {
            "columns": [
                {"name": "id",   "null_fraction": 0.0},
                {"name": "status", "null_fraction": 0.40},  # user tuned
            ]
        }
        theirs = {
            "columns": [
                {"name": "id",   "null_fraction": 0.01},   # collect updated
                {"name": "status", "null_fraction": 0.0},
            ]
        }
        merged, conflicts = merge(base, ours, theirs)
        cols = {c["name"]: c for c in merged["columns"]}
        assert cols["id"]["null_fraction"] == 0.01     # collect update applied
        assert cols["status"]["null_fraction"] == 0.40  # user edit preserved
        assert conflicts == []

    def test_collect_adds_new_column(self):
        base = {"columns": [{"name": "id", "type": "int"}]}
        ours = {"columns": [{"name": "id", "type": "int"}]}
        theirs = {
            "columns": [
                {"name": "id",       "type": "int"},
                {"name": "new_col",  "type": "text"},  # collect discovered new col
            ]
        }
        merged, conflicts = merge(base, ours, theirs)
        names = [c["name"] for c in merged["columns"]]
        assert "new_col" in names
        assert conflicts == []

    def test_user_adds_column_collect_also_adds_different(self):
        base   = {"columns": [{"name": "id"}]}
        ours   = {"columns": [{"name": "id"}, {"name": "user_col"}]}
        theirs = {"columns": [{"name": "id"}, {"name": "collect_col"}]}
        merged, conflicts = merge(base, ours, theirs)
        names = [c["name"] for c in merged["columns"]]
        assert "user_col" in names
        assert "collect_col" in names
        assert conflicts == []

    def test_user_removes_column(self):
        base = {
            "columns": [
                {"name": "id"},
                {"name": "deprecated"},
            ]
        }
        ours   = {"columns": [{"name": "id"}]}              # user removed deprecated
        theirs = {"columns": [{"name": "id"}, {"name": "deprecated"}]}  # collect unchanged
        merged, conflicts = merge(base, ours, theirs)
        names = [c["name"] for c in merged["columns"]]
        assert "deprecated" not in names
        assert conflicts == []

    def test_ordering_follows_ours_then_new(self):
        """New items from theirs appear after ours items."""
        base   = {"tables": [{"name": "A"}, {"name": "B"}]}
        ours   = {"tables": [{"name": "B"}, {"name": "A"}]}  # user reordered
        theirs = {"tables": [{"name": "A"}, {"name": "B"}, {"name": "C"}]}  # collect added C
        merged, conflicts = merge(base, ours, theirs)
        names = [t["name"] for t in merged["tables"]]
        assert names.index("B") < names.index("A")  # ours ordering preserved
        assert "C" in names
        assert conflicts == []


# ── Realistic statschema-like schema ─────────────────────────────────────────


class TestStatschemaShape:
    """Merge tests using the shape of statschema schema.yaml."""

    BASE = {
        "version": "1.0",
        "tables": [
            {
                "name": "account",
                "row_count_per_sf": 100000,
                "table_type": "fact",
                "columns": [
                    {"name": "aid",      "null_fraction": 0.0,  "n_distinct": -1.0},
                    {"name": "bid",      "null_fraction": 0.0,  "n_distinct": 1.0},
                    {"name": "abalance", "null_fraction": 0.0,  "n_distinct": -0.9},
                ],
            },
            {
                "name": "branch",
                "row_count_per_sf": 1,
                "table_type": "dim",
                "columns": [
                    {"name": "bid",      "null_fraction": 0.0, "n_distinct": -1.0},
                    {"name": "bbalance", "null_fraction": 0.0, "n_distinct": -0.8},
                ],
            },
        ],
    }

    def test_user_changes_table_type_collect_updates_row_count(self):
        base = copy.deepcopy(self.BASE)
        ours = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        # User corrects table_type for account
        ours["tables"][0]["table_type"] = "dim"
        # Collect updates row_count_per_sf from a new run
        theirs["tables"][0]["row_count_per_sf"] = 120000

        merged, conflicts = merge(base, ours, theirs)
        tables = {t["name"]: t for t in merged["tables"]}
        assert tables["account"]["table_type"] == "dim"        # user's correction
        assert tables["account"]["row_count_per_sf"] == 120000  # collect update
        assert conflicts == []

    def test_user_tunes_weight_collect_updates_null_fraction(self):
        base = copy.deepcopy(self.BASE)
        ours = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        # User tweaks n_distinct on abalance to stress-test distribution
        ours["tables"][0]["columns"][2]["n_distinct"] = -0.5
        # Collect measures updated null_fraction on bid
        theirs["tables"][0]["columns"][1]["null_fraction"] = 0.02

        merged, conflicts = merge(base, ours, theirs)
        tables = {t["name"]: t for t in merged["tables"]}
        cols = {c["name"]: c for c in tables["account"]["columns"]}
        assert cols["abalance"]["n_distinct"] == -0.5   # user preserved
        assert cols["bid"]["null_fraction"] == 0.02     # collect applied
        assert conflicts == []

    def test_both_change_same_stat_is_conflict(self):
        base = copy.deepcopy(self.BASE)
        ours = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["tables"][0]["columns"][0]["null_fraction"] = 0.10
        theirs["tables"][0]["columns"][0]["null_fraction"] = 0.05

        merged, conflicts = merge(base, ours, theirs)
        assert len(conflicts) == 1
        assert conflicts[0].ours_val == 0.10
        assert conflicts[0].theirs_val == 0.05


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_dicts(self):
        merged, conflicts = merge({}, {}, {})
        assert merged == {}
        assert conflicts == []

    def test_none_values(self):
        base   = {"a": None}
        ours   = {"a": None}
        theirs = {"a": 42}
        merged, conflicts = merge(base, ours, theirs)
        assert merged["a"] == 42
        assert conflicts == []

    def test_deeply_nested(self):
        base   = {"a": {"b": {"c": {"d": 1}}}}
        ours   = {"a": {"b": {"c": {"d": 9}}}}  # user changed deep value
        theirs = {"a": {"b": {"c": {"d": 1}}}}  # collect unchanged
        merged, conflicts = merge(base, ours, theirs)
        assert merged["a"]["b"]["c"]["d"] == 9
        assert conflicts == []

    def test_mixed_types_conflict(self):
        base   = {"x": 1}
        ours   = {"x": "string"}
        theirs = {"x": [1, 2, 3]}
        merged, conflicts = merge(base, ours, theirs)
        assert len(conflicts) == 1

    def test_returns_independent_copy(self):
        """Mutating the merged result does not affect inputs."""
        base   = {"a": {"b": 1}}
        ours   = {"a": {"b": 1}}
        theirs = {"a": {"b": 2}}
        merged, _ = merge(base, ours, theirs)
        merged["a"]["b"] = 999
        assert theirs["a"]["b"] == 2
