import hashlib
from pathlib import Path
import tempfile
import unittest

from editor_core import EditSession
import readonly_inspector


def make_constant(unique_id: str, title: str, address: int) -> str:
    return f'''<XDFCONSTANT uniqueid="{unique_id}">
      <title>{title}</title>
      <description>Fuel calibration fixture</description>
      <CATEGORYMEM category="1" />
      <XDFAXIS id="z">
        <EMBEDDEDDATA mmedaddress="0x{address:x}" mmedtypeflags="0x00" mmedelementsizebits="8" mmedrowcount="1" mmedcolcount="1" />
        <MATH equation="X" />
        <units>value</units>
        <decimalpl>0</decimalpl>
      </XDFAXIS>
    </XDFCONSTANT>'''


def fixture_files(root: Path):
    xdf = root / 'fixture.xdf'
    bin_file = root / 'fixture.bin'
    xdf.write_text('''<XDFFORMAT>
      <XDFHEADER><CATEGORY index="1" name="Fueling" /></XDFHEADER>
      ''' + make_constant('b1', 'Fuel (Main) (Bank 1)', 0) +
      make_constant('b2', 'Fuel (Main) (Bank 2)', 1) +
      make_constant('m2', 'Fuel (Main) (Map 2)', 2) +
      make_constant('m3', 'Fuel (Main) (Map 3)', 3) +
      '''</XDFFORMAT>''', encoding='utf-8')
    bin_file.write_bytes(bytes([10, 10, 12, 10]))
    return xdf, bin_file


class ReadOnlyInspectorTests(unittest.TestCase):
    def test_workspace_catalog_show_and_immutability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xdf, bin_file = fixture_files(root)
            original_hashes = (hashlib.sha256(xdf.read_bytes()).hexdigest(), hashlib.sha256(bin_file.read_bytes()).hexdigest())
            workspace_path = root / 'tune.inspect.json'
            opened = readonly_inspector.open_workspace(xdf, bin_file, workspace_path)
            manifest, session = readonly_inspector.load_workspace(workspace_path)

            result = readonly_inspector.catalog(session)
            self.assertEqual(4, result['matched_count'])
            self.assertNotIn('values', result['tables'][0]['x_axis'] or {})

            family = readonly_inspector.compare_maps(session, 'Fuel (Main)')
            map1_members = [item for item in family['variants'] if item['map'] == 'Map 1']
            self.assertEqual(2, len(map1_members))
            self.assertEqual(4, len(family['comparisons']))
            self.assertTrue(all(item['status'] == 'aligned' for item in family['comparisons']))
            self.assertTrue(all(item['changed_cell_count'] in (0, 1) for item in family['comparisons']))

            selected = readonly_inspector.show_table(session, family='Fuel (Main)', map_name='Map 2')
            self.assertEqual([[12.0]], selected['table']['values'])
            self.assertEqual(manifest['workspace_id'], opened['workspace']['workspace_id'])
            self.assertEqual(original_hashes, (hashlib.sha256(xdf.read_bytes()).hexdigest(), hashlib.sha256(bin_file.read_bytes()).hexdigest()))

    def test_workspace_rejects_changed_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xdf, bin_file = fixture_files(root)
            workspace = root / 'tune.inspect.json'
            readonly_inspector.open_workspace(xdf, bin_file, workspace)
            bin_file.write_bytes(bytes([1, 2, 3, 4]))
            with self.assertRaisesRegex(ValueError, 'changed since'):
                readonly_inspector.load_workspace(workspace)

    def test_open_does_not_overwrite_existing_workspace_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xdf, bin_file = fixture_files(root)
            workspace = root / 'existing.bin'
            original = b'keep this existing file'
            workspace.write_bytes(original)
            with self.assertRaisesRegex(FileExistsError, 'choose a new manifest path'):
                readonly_inspector.open_workspace(xdf, bin_file, workspace)
            self.assertEqual(original, workspace.read_bytes())

    def test_comparison_refuses_to_diff_misaligned_axes(self):
        left = {'table_id': 'a', 'title': 'A', 'rows': 1, 'cols': 2, 'grid': [[1, 2]], 'x_labels': [10, 20], 'y_labels': [1]}
        right = {'table_id': 'b', 'title': 'B', 'rows': 1, 'cols': 2, 'grid': [[1, 3]], 'x_labels': [10, 30], 'y_labels': [1]}
        result = readonly_inspector.compare_table_pair(left, right)
        self.assertEqual('not_aligned', result['status'])
        self.assertIsNone(result['changed_cell_count'])
        self.assertEqual(['x_axis'], [item['kind'] for item in result['alignment_issues']])


if __name__ == '__main__':
    unittest.main()
