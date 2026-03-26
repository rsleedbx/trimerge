"""Tests for trimerge.yaml_io — YAML file merge with comment preservation."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("ruamel.yaml", reason="ruamel.yaml not installed; install with: pip install 'trimerge[yaml]'")

from ruamel.yaml import YAML  # noqa: E402
from ruamel.yaml.comments import CommentedMap  # noqa: E402

from trimerge import merge_files, save_merged  # noqa: E402
from trimerge.conflicts import Conflict  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"

# ── helpers ───────────────────────────────────────────────────────────────────


def _write_yaml(path: Path, data: str) -> None:
    path.write_text(data, encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    y = YAML()
    return y.load(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── basic merge via files ─────────────────────────────────────────────────────


class TestMergeFiles:
    def test_no_conflict_applies_both_changes(self, tmp_path):
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"

        _write_yaml(base,   "a: 1\nb: 2\n")
        _write_yaml(ours,   "a: 9\nb: 2\n")   # user changed a
        _write_yaml(theirs, "a: 1\nb: 5\n")   # collect changed b

        merged, conflicts = merge_files(base, ours, theirs)
        assert conflicts == []
        assert merged["a"] == 9
        assert merged["b"] == 5

    def test_returns_commented_map(self, tmp_path):
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"
        for f in (base, ours, theirs):
            _write_yaml(f, "a: 1\n")

        merged, _ = merge_files(base, ours, theirs)
        assert isinstance(merged, CommentedMap)

    def test_conflict_detected(self, tmp_path):
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"

        _write_yaml(base,   "x: 1\n")
        _write_yaml(ours,   "x: 9\n")
        _write_yaml(theirs, "x: 2\n")

        merged, conflicts = merge_files(base, ours, theirs)
        assert len(conflicts) == 1
        assert merged["x"] == 9  # ours wins

    def test_collect_adds_new_key(self, tmp_path):
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"

        _write_yaml(base,   "a: 1\n")
        _write_yaml(ours,   "a: 1\n")
        _write_yaml(theirs, "a: 1\nnew_key: added_by_collect\n")

        merged, conflicts = merge_files(base, ours, theirs)
        assert merged["new_key"] == "added_by_collect"
        assert conflicts == []

    def test_user_delete_honored(self, tmp_path):
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"

        _write_yaml(base,   "a: 1\nb: 2\n")
        _write_yaml(ours,   "a: 1\n")          # user deleted b
        _write_yaml(theirs, "a: 1\nb: 2\n")    # collect unchanged

        merged, conflicts = merge_files(base, ours, theirs)
        assert "b" not in merged
        assert conflicts == []


# ── comment preservation ──────────────────────────────────────────────────────


class TestCommentPreservation:
    def test_user_comment_survives_on_unchanged_key(self, tmp_path):
        """A comment on a key the user did not change should survive the merge."""
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"

        _write_yaml(base,   "null_fraction: 0.0\nn_distinct: 100.0\n")
        _write_yaml(ours,   "null_fraction: 0.0\nn_distinct: 100.0  # user annotated\n")
        _write_yaml(theirs, "null_fraction: 0.05\nn_distinct: 100.0\n")  # collect updated null_fraction

        merged, conflicts = merge_files(base, ours, theirs)
        assert conflicts == []

        out = tmp_path / "result.yaml"
        save_merged(merged, out)
        content = _read_text(out)
        assert "user annotated" in content, "User comment should be preserved"

    def test_user_comment_survives_when_collect_updates_other_key(self, tmp_path):
        yaml_base = (
            "version: '1.0'\n"
            "tables:\n"
            "  - name: account\n"
            "    row_count_per_sf: 100000\n"
            "    table_type: fact\n"
        )
        yaml_ours = (
            "version: '1.0'\n"
            "tables:\n"
            "  - name: account\n"
            "    row_count_per_sf: 100000\n"
            "    table_type: dim  # corrected by hand\n"
        )
        yaml_theirs = (
            "version: '1.0'\n"
            "tables:\n"
            "  - name: account\n"
            "    row_count_per_sf: 120000\n"  # collect updated row count
            "    table_type: fact\n"
        )

        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"
        _write_yaml(base,   yaml_base)
        _write_yaml(ours,   yaml_ours)
        _write_yaml(theirs, yaml_theirs)

        merged, conflicts = merge_files(base, ours, theirs)
        assert conflicts == []

        out = tmp_path / "result.yaml"
        save_merged(merged, out)
        content = _read_text(out)
        assert "corrected by hand" in content


# ── save_merged ───────────────────────────────────────────────────────────────


class TestSaveMerged:
    def test_roundtrip_valid_yaml(self, tmp_path):
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"
        out    = tmp_path / "out.yaml"

        _write_yaml(base,   "a: 1\nb: 2\n")
        _write_yaml(ours,   "a: 9\nb: 2\n")
        _write_yaml(theirs, "a: 1\nb: 5\n")

        merged, _ = merge_files(base, ours, theirs)
        save_merged(merged, out)

        result = _read_yaml(out)
        assert result["a"] == 9
        assert result["b"] == 5

    def test_creates_file(self, tmp_path):
        base   = tmp_path / "base.yaml"
        ours   = tmp_path / "ours.yaml"
        theirs = tmp_path / "theirs.yaml"
        out    = tmp_path / "out.yaml"

        for f in (base, ours, theirs):
            _write_yaml(f, "x: 1\n")

        merged, _ = merge_files(base, ours, theirs)
        save_merged(merged, out)
        assert out.exists()


# ── realistic statschema fixture ─────────────────────────────────────────────


class TestStatschemaFixture:
    """Integration test using the real tpcb_schema.yaml from statschema."""

    def test_user_edits_table_type_collect_updates_row_count(self, tmp_path):
        import copy
        from ruamel.yaml import YAML as _YAML

        y = _YAML()
        with open(FIXTURES / "tpcb_schema.yaml", encoding="utf-8") as f:
            schema = y.load(f)

        base_path   = tmp_path / "base.yaml"
        ours_path   = tmp_path / "ours.yaml"
        theirs_path = tmp_path / "theirs.yaml"

        # Write base (pristine schema)
        save_merged(schema, base_path)

        # ours: user adds table_type annotations and tuned a generation param
        import copy as _copy
        from ruamel.yaml.comments import CommentedMap

        ours_data = y.load(base_path.read_text())
        for tbl in ours_data["tables"]:
            tbl["table_type"] = "dim" if tbl["name"] == "branch" else "fact"
        save_merged(ours_data, ours_path)

        # theirs: collect updated row_count_per_sf for account
        theirs_data = y.load(base_path.read_text())
        for tbl in theirs_data["tables"]:
            if tbl["name"] == "account":
                tbl["row_count_per_sf"] = 110000
        save_merged(theirs_data, theirs_path)

        merged, conflicts = merge_files(base_path, ours_path, theirs_path)
        assert conflicts == []

        tables = {t["name"]: t for t in merged["tables"]}
        assert tables["branch"]["table_type"] == "dim"
        assert tables["account"]["table_type"] == "fact"
        assert tables["account"]["row_count_per_sf"] == 110000

    def test_no_spurious_conflicts_on_identical_reload(self, tmp_path):
        """Re-collecting an identical schema produces zero conflicts."""
        from ruamel.yaml import YAML as _YAML

        y = _YAML()
        with open(FIXTURES / "tpcb_schema.yaml", encoding="utf-8") as f:
            schema = y.load(f)

        p = tmp_path / "schema.yaml"
        save_merged(schema, p)

        # All three files are identical → no conflicts
        merged, conflicts = merge_files(p, p, p)
        assert conflicts == []
