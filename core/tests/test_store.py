"""The local store, and the migrations that let it grow."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from osuforge.hardware import Mouse, Profile, ProfileError, Tracking
from osuforge.hardware import save as save_json
from osuforge.store.db import (
    SCHEMA_VERSION,
    DeviceRow,
    StoreError,
    default_db_path,
    open_store,
)
from osuforge.store.profile import (
    POINTER_ROLE,
    import_legacy_profile,
    load,
    load_from,
    save,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "osu-forge.db"


class TestSchema:
    def test_a_first_run_creates_the_file_and_its_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "osu-forge.db"
        with open_store(path) as store:
            assert store.version == SCHEMA_VERSION
        assert path.is_file()

    def test_reopening_does_not_migrate_again(self, db: Path) -> None:
        with open_store(db) as store:
            store.put("marker", 1)
        with open_store(db) as store:
            assert store.get("marker") == 1
            assert store.version == SCHEMA_VERSION

    def test_a_newer_schema_is_refused_rather_than_partly_read(self, db: Path) -> None:
        # The failure this whole design exists to avoid: reading the fields that
        # happen to be recognisable, then writing the file back without whatever
        # the newer build added.
        with open_store(db):
            pass
        connection = sqlite3.connect(db)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        connection.commit()
        connection.close()

        with pytest.raises(StoreError, match="newer osu-forge"), open_store(db):
            pass

    def test_the_default_lives_beside_the_journal_not_inside_osu(self, monkeypatch) -> None:
        monkeypatch.delenv("OSU_FORGE_DB", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
        path = default_db_path()
        assert path.parent.name == "osu-forge"
        assert "osu!" not in str(path)

    def test_the_env_override_wins(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("OSU_FORGE_DB", str(tmp_path / "elsewhere.db"))
        assert default_db_path() == tmp_path / "elsewhere.db"


class TestDevices:
    def test_a_device_round_trips_with_its_attribute_bag(self, db: Path) -> None:
        with open_store(db) as store:
            store.put_device(
                DeviceRow(
                    role="pointer",
                    kind="mouse",
                    vendor="Razer",
                    model="Viper V3 Pro",
                    attributes={"dpi_x": 1400, "dpi_y": 1350},
                )
            )
            found = store.device("pointer")
        assert found is not None
        assert found.model == "Viper V3 Pro"
        assert found.attributes == {"dpi_x": 1400, "dpi_y": 1350}

    def test_an_unknown_kind_stores_and_reads_back_unchanged(self, db: Path) -> None:
        # The extensibility claim, tested rather than asserted in a docstring:
        # a device kind this build has never heard of needs no schema change.
        with open_store(db) as store:
            store.put_device(
                DeviceRow(
                    role="pointer",
                    kind="tablet",
                    vendor="Wacom",
                    model="CTL-472",
                    attributes={"area_w_mm": 100.0, "area_h_mm": 56.0},
                )
            )
            found = store.device("pointer")
        assert found is not None
        assert found.kind == "tablet"
        assert found.attributes["area_w_mm"] == 100.0

    def test_a_role_holds_one_device_not_a_history(self, db: Path) -> None:
        # A previous mouse is not evidence about the current one, and keeping it
        # would invite an analysis to average across a hardware change.
        with open_store(db) as store:
            store.put_device(DeviceRow("pointer", "mouse", "", "", {"dpi_x": 800, "dpi_y": 800}))
            store.put_device(DeviceRow("pointer", "mouse", "", "", {"dpi_x": 1600, "dpi_y": 1600}))
            found = store.device("pointer")
            count = store.connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        assert found is not None and found.attributes["dpi_x"] == 1600
        assert count == 1

    def test_an_absent_device_is_none(self, db: Path) -> None:
        with open_store(db) as store:
            assert store.device("pointer") is None
            assert not store.forget_device("pointer")

    def test_corrupt_attributes_are_refused_rather_than_returned_empty(self, db: Path) -> None:
        with open_store(db) as store:
            store.connection.execute(
                "INSERT INTO devices (role, kind, attributes) VALUES ('pointer', 'mouse', '{oops')"
            )
            store.connection.commit()
            with pytest.raises(StoreError, match="unreadable attributes"):
                store.device("pointer")


class TestSettings:
    def test_values_keep_their_type(self, db: Path) -> None:
        with open_store(db) as store:
            store.put("a", 5)
            store.put("b", [1, 2])
            store.put("c", {"x": True})
            assert store.get("a") == 5
            assert store.get("b") == [1, 2]
            assert store.get("c") == {"x": True}

    def test_an_absent_key_returns_the_default(self, db: Path) -> None:
        with open_store(db) as store:
            assert store.get("nothing") is None
            assert store.get("nothing", 42) == 42

    def test_writing_twice_replaces(self, db: Path) -> None:
        with open_store(db) as store:
            store.put("k", "first")
            store.put("k", "second")
            assert store.get("k") == "second"


class TestProfile:
    def test_a_profile_round_trips(self, db: Path) -> None:
        original = Profile(pointer=Mouse(1400, 1350), tracking=Tracking.asymmetric(1))
        with open_store(db) as store:
            save(store, original, vendor="Razer", model="Viper")
            assert load(store) == original
            row = store.device(POINTER_ROLE)
        assert row is not None and row.vendor == "Razer"

    def test_an_empty_store_is_an_empty_profile(self, db: Path) -> None:
        with open_store(db) as store:
            assert load(store) == Profile()

    def test_a_stored_row_is_validated_like_any_other_input(self, db: Path) -> None:
        # Being in the database is not a reason to trust a value. An impossible
        # denominator turns a real measurement into a confident wrong answer
        # whether it arrived from a form or from a text editor.
        with open_store(db) as store:
            store.put_device(DeviceRow(POINTER_ROLE, "mouse", "", "", {"dpi_x": 3, "dpi_y": 3}))
            with pytest.raises(ProfileError, match="typo rather than a mouse"):
                load(store)

    def test_an_unsupported_kind_is_refused_not_read_as_absent(self, db: Path) -> None:
        with open_store(db) as store:
            store.put_device(DeviceRow(POINTER_ROLE, "tablet", "", "", {"area_w_mm": 100}))
            with pytest.raises(ProfileError, match="'tablet' is not supported"):
                load(store)

    def test_tracking_survives_without_a_pointer(self, db: Path) -> None:
        with open_store(db) as store:
            save(store, Profile(tracking=Tracking(label="High")))
            loaded = load(store)
        assert loaded.pointer is None
        assert loaded.tracking == Tracking(label="High")


class TestLegacyImport:
    def test_an_existing_json_profile_is_brought_across(self, db: Path, tmp_path: Path) -> None:
        legacy = tmp_path / "profile.json"
        save_json(Profile(pointer=Mouse(1400, 1350), tracking=Tracking.asymmetric(1)), legacy)
        with open_store(db) as store:
            assert import_legacy_profile(store, legacy)
            assert load(store).mouse == Mouse(1400, 1350)

    def test_it_does_not_overwrite_what_the_store_already_holds(
        self, db: Path, tmp_path: Path
    ) -> None:
        # The database is authoritative once it has a pointer. Re-importing
        # would quietly replace a newer value with an older file.
        legacy = tmp_path / "profile.json"
        save_json(Profile(pointer=Mouse(800, 800)), legacy)
        with open_store(db) as store:
            save(store, Profile(pointer=Mouse(1600, 1600)))
            assert not import_legacy_profile(store, legacy)
            assert load(store).mouse == Mouse(1600, 1600)

    def test_an_absent_legacy_file_is_not_an_error(self, db: Path, tmp_path: Path) -> None:
        with open_store(db) as store:
            assert not import_legacy_profile(store, tmp_path / "nothing.json")

    def test_load_from_opens_imports_and_reads(self, db: Path, tmp_path: Path, monkeypatch) -> None:
        legacy = tmp_path / "profile.json"
        save_json(Profile(pointer=Mouse(1400, 1350)), legacy)
        monkeypatch.setenv("OSU_FORGE_PROFILE", str(legacy))
        assert load_from(db).mouse == Mouse(1400, 1350)


class TestNothingLeaksIntoOsu:
    def test_the_written_file_is_only_the_store(self, db: Path, tmp_path: Path) -> None:
        with open_store(db) as store:
            save(store, Profile(pointer=Mouse(800, 800)))
        assert {p.name for p in tmp_path.iterdir()} == {"osu-forge.db"}

    def test_attributes_are_stored_as_json_not_as_a_repr(self, db: Path) -> None:
        # A repr would round-trip through eval or not at all. Asserted because
        # the column is TEXT and nothing else would catch the difference.
        with open_store(db) as store:
            store.put_device(DeviceRow("pointer", "mouse", "", "", {"dpi_x": 800, "dpi_y": 800}))
            raw = store.connection.execute(
                "SELECT attributes FROM devices WHERE role = 'pointer'"
            ).fetchone()[0]
        assert json.loads(raw) == {"dpi_x": 800, "dpi_y": 800}
