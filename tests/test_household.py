"""My home: local SQLite persistence for saved appliances."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from homemanual.household import (  # noqa: E402
    MAX_APPLIANCES,
    MAX_NICKNAME_CHARS,
    HouseholdError,
    HouseholdStore,
)
from homemanual.index import ManualIndex  # noqa: E402
from tests.support import sample_library  # noqa: E402


class HouseholdStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_household"
        self.tmp.mkdir(exist_ok=True)
        self.db = self.tmp / "household.db"
        self.index = sample_library()
        self.store = HouseholdStore(path=self.db, index=self.index)

    def tearDown(self) -> None:
        for item in sorted(self.tmp.glob("*")):
            item.unlink()
        self.tmp.rmdir()

    # -- basics -------------------------------------------------------
    def test_add_list_rename_remove_roundtrip(self):
        added = self.store.add("Kitchen dishwasher", "dishwasher", "GD3000")
        self.assertEqual(added["nickname"], "Kitchen dishwasher")
        self.assertEqual(added["model"], "GD3000")
        self.assertEqual(added["doc_id"], "gamma-dishwasher")
        self.assertTrue(added["supported"])
        self.assertTrue(added["id"].startswith("app_"))

        listed = self.store.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], added["id"])

        renamed = self.store.rename(added["id"], "  Upstairs   dishwasher ")
        self.assertEqual(renamed["nickname"], "Upstairs dishwasher")
        self.assertNotEqual(renamed["updated_at"], "")

        self.assertTrue(self.store.remove(added["id"]))
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.store.remove(added["id"]))

    def test_records_survive_a_restart(self):
        added = self.store.add("Basement washer", "washer", "AW1000")
        reopened = HouseholdStore(path=self.db, index=self.index)
        self.assertEqual([item["id"] for item in reopened.list()], [added["id"]])
        self.assertEqual(reopened.get(added["id"])["model"], "AW1000")

    def test_ids_are_unique_and_stable(self):
        first = self.store.add("Washer", "washer", "AW1000")
        second = self.store.add("Dryer", "dryer", "BD2000")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.get(first["id"])["id"], first["id"])

    def test_migration_is_idempotent(self):
        self.store.migrate()
        self.store.migrate()
        self.assertEqual(self.store.list(), [])

    # -- validation ---------------------------------------------------
    def test_unknown_model_is_refused_and_nothing_is_saved(self):
        with self.assertRaises(HouseholdError) as context:
            self.store.add("Ghost washer", "washer", "ZZ9000XX")
        self.assertEqual(context.exception.code, "unsupported_model")
        self.assertEqual(self.store.list(), [])

    def test_model_from_another_category_is_refused(self):
        with self.assertRaises(HouseholdError) as context:
            self.store.add("Wrong type", "dryer", "AW1000")
        self.assertEqual(context.exception.code, "unsupported_model")

    def test_nickname_bounds_and_content_are_enforced(self):
        for bad in ("", "   ", "x" * (MAX_NICKNAME_CHARS + 1), "bad\x00name", None, 7):
            with self.assertRaises(HouseholdError, msg=repr(bad)) as context:
                self.store.add(bad, "washer", "AW1000")
            self.assertEqual(context.exception.code, "invalid_name")
        self.assertEqual(self.store.list(), [])

    def test_unknown_category_is_refused(self):
        with self.assertRaises(HouseholdError) as context:
            self.store.add("Spaceship", "spaceship", "AW1000")
        self.assertEqual(context.exception.code, "invalid_category")

    def test_missing_model_is_refused(self):
        for bad in ("", None, 5):
            with self.assertRaises(HouseholdError) as context:
                self.store.add("Washer", "washer", bad)
            self.assertIn(context.exception.code, {"invalid_model", "unsupported_model"})

    def test_rename_rejects_a_missing_appliance(self):
        with self.assertRaises(HouseholdError) as context:
            self.store.rename("app_000000000000", "nope")
        self.assertEqual(context.exception.code, "unknown_appliance")

    # -- SQL safety ---------------------------------------------------
    def test_nickname_is_parameterised_not_interpolated(self):
        hostile = 'x"; DROP TABLE appliances;--'
        added = self.store.add(hostile, "washer", "AW1000")
        self.assertEqual(added["nickname"], hostile)
        self.assertEqual(len(self.store.list()), 1)
        # The table still exists and still holds exactly one row.
        self.assertEqual(self.store.get(added["id"])["nickname"], hostile)

    def test_appliance_cap_is_enforced(self):
        for number in range(MAX_APPLIANCES):
            self.store.add(f"Washer {number}", "washer", "AW1000")
        with self.assertRaises(HouseholdError) as context:
            self.store.add("One too many", "washer", "AW1000")
        self.assertEqual(context.exception.code, "too_many")

    # -- library drift ------------------------------------------------
    def test_stale_record_fails_explicitly_after_the_library_changes(self):
        added = self.store.add("Old washer", "washer", "AW1000")
        shrunk = ManualIndex(docs=[], chunks=[], built_at="test")
        stale = HouseholdStore(path=self.db, index=shrunk)
        record = stale.get(added["id"])
        self.assertFalse(record["supported"])
        with self.assertRaises(HouseholdError) as context:
            stale.resolve(added["id"])
        self.assertEqual(context.exception.code, "unsupported_appliance")

    def test_resolve_returns_category_and_model(self):
        added = self.store.add("Kitchen dishwasher", "dishwasher", "GD3000")
        self.assertEqual(self.store.resolve(added["id"]), ("dishwasher", "GD3000"))


if __name__ == "__main__":
    unittest.main()
