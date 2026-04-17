from pathlib import Path
import tempfile
import unittest

from editor_core import normalize_paste_values
from project_store import ProjectStore, build_project_export_path, split_revision_token


class ProjectStoreTests(unittest.TestCase):
    def test_normalize_paste_values_requires_numeric_rows(self):
        self.assertEqual([[1.0, 2.5], [3.0]], normalize_paste_values([["1", "2.5"], [3]]))
        with self.assertRaises(ValueError):
            normalize_paste_values([["1", "nope"]])

    def test_project_round_trip_and_active_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(Path(tmp) / "projects.json")
            project = store.upsert_project({
                "name": "Street tune",
                "xdf_path": str(Path(tmp) / "n55.xdf"),
                "bin_folder": tmp,
                "last_bin_path": str(Path(tmp) / "base_v3.bin"),
                "output_folder": tmp,
                "log_folder": str(Path(tmp) / "logs"),
                "save_template": "{source_stem}_{token}",
                "remote_save_mode": "small_edit",
            })

            loaded = store.get_active_project()
            self.assertEqual(project["id"], loaded["id"])
            self.assertEqual("small_edit", loaded["remote_save_mode"])
            self.assertEqual(str(Path(tmp) / "logs"), loaded["log_folder"])

    def test_new_revision_increments_detected_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = {
                "last_bin_path": str(Path(tmp) / "pump_mk3.bin"),
                "output_folder": tmp,
                "save_template": "{source_stem}{token}({description})",
                "description": "KillMap",
                "remote_save_mode": "new_revision",
                "revision_number": 1,
                "revision_prefix": "v",
            }

            output_path, metadata = build_project_export_path(project)

            self.assertEqual("pumpmk4(KillMap).bin", output_path.name)
            self.assertEqual("mk4", metadata["token"])
            self.assertEqual(5, metadata["next_revision_number"])

    def test_revision_description_format_uses_current_file_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = {
                "last_bin_path": str(Path(tmp) / "E85v6(InjectorFix).bin"),
                "output_folder": tmp,
                "save_template": "{source_stem}{token}({description})",
                "description": "",
                "remote_save_mode": "new_revision",
                "revision_number": 2,
                "revision_prefix": "v",
            }

            output_path, metadata = build_project_export_path(project, description="Fueling&WGDC")

            self.assertEqual("E85v7(Fueling-WGDC).bin", output_path.name)
            self.assertEqual("v7", metadata["token"])
            self.assertEqual(8, metadata["next_revision_number"])

    def test_revision_parser_ignores_bad_trailing_revision_suffix(self):
        parsed = split_revision_token("E85v6(InjectorFix)v20")

        self.assertEqual("E85", parsed["base_stem"])
        self.assertEqual("v", parsed["prefix"])
        self.assertEqual(6, parsed["number"])
        self.assertEqual("InjectorFix", parsed["description"])

    def test_small_edit_reuses_existing_edit_file_and_does_not_advance_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "E85v4(KillMap).bin"
            project = {
                "last_bin_path": str(Path(tmp) / "E85v4.bin"),
                "output_folder": tmp,
                "save_template": "{source_stem}{token}({description})",
                "description": "KillMap",
                "remote_save_mode": "small_edit",
                "small_edit_output_path": str(existing),
                "revision_number": 5,
                "revision_prefix": "v",
            }

            output_path, metadata = build_project_export_path(project)

            self.assertEqual(existing, output_path)
            self.assertEqual(5, metadata["next_revision_number"])


if __name__ == "__main__":
    unittest.main()
