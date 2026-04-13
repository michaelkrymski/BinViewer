# E85v2 Parity Regression

## Goal

Prove that the three editing paths produce the exact same final BIN:

1. TunerPro manual editing
2. Web interface editing/export
3. CLI/editor-core replay

This is the canonical recorded regression case for future parity testing.

## Reference Files

XDF:
- `C:\Users\Michael Krymski\Documents\N55Maps\Tools\000021571DAA01.xdf`

Base BIN used for replay:
- `C:\Users\Michael Krymski\Documents\N55Maps\Tools\Stock - Copy.bin`

TunerPro final BIN:
- `C:\Users\Michael Krymski\Documents\N55Maps\E85v2.bin`

Web exports used during validation:
- `C:\Users\Michael Krymski\Downloads\Stock---Copy-edited (1).zip`
- `C:\Users\Michael Krymski\Downloads\Stock---Copy-edited-edited.zip`

Recorded replay outputs:
- `C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\cli_replay_E85v2.bin`
- `C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\cli_replay_E85v2.bin.changes.json`

## Final Expected Result

Final expected SHA-256 for all matching outputs:

- `495fea5a8ddb7e187d8cad8e9151c6e8023e2ae18d91e27a9ceb2113fb42dd01`

Final expected compare result:

- raw byte diff count: `0`
- changed XDF-defined tables: `0`
- raw diff ranges: `0`

## What Was Validated

The final matched state required:

1. Initial web export captured the main bulk edit set.
2. A second web export captured the three missed antilag table edits:
   - `WGDC Base (Custom) (Antilag)` -> all cells set to `0`
   - `WGDC P-Factor (Custom) (Antilag)` -> all cells set to `0`
   - `WGDC D-Factor (Custom) (Antilag)` -> all cells set to `0`
3. The final web export matched the saved TunerPro BIN exactly.
4. Replaying the combined web changelog onto `Stock - Copy.bin` through `editor_core.py` also matched the saved TunerPro BIN exactly.

## Quick Verification Commands

Run from:

- `C:\Users\Michael Krymski\Documents\TunerPro Converter`

### 1. Compare final web export vs TunerPro

Extract the final web ZIP:

```powershell
Expand-Archive -LiteralPath 'C:\Users\Michael Krymski\Downloads\Stock---Copy-edited-edited.zip' `
  -DestinationPath 'C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\web_export_latest_v2' -Force
```

Byte-for-byte compare:

```powershell
@'
from pathlib import Path
import hashlib

web_bin = Path(r'C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\web_export_latest_v2\Stock---Copy-edited-edited.bin')
manual_bin = Path(r'C:\Users\Michael Krymski\Documents\N55Maps\E85v2.bin')

wb = web_bin.read_bytes()
mb = manual_bin.read_bytes()

print('same_size', len(wb) == len(mb))
print('same_hash', hashlib.sha256(wb).hexdigest() == hashlib.sha256(mb).hexdigest())
print('sha256', hashlib.sha256(wb).hexdigest())
print('diff_count', sum(1 for a, b in zip(wb, mb) if a != b))
'@ | .\.venv\Scripts\python.exe -
```

Expected:

- `same_size True`
- `same_hash True`
- SHA-256 `495fea5a8ddb7e187d8cad8e9151c6e8023e2ae18d91e27a9ceb2113fb42dd01`
- `diff_count 0`

XDF-aware compare:

```powershell
.\.venv\Scripts\python.exe tuner_cli.py --json compare `
  'C:\Users\Michael Krymski\Documents\N55Maps\Tools\000021571DAA01.xdf' `
  'C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\web_export_latest_v2\Stock---Copy-edited-edited.bin' `
  'C:\Users\Michael Krymski\Documents\N55Maps\E85v2.bin'
```

Expected summary:

- `changed_table_count: 0`
- `raw_change_range_count: 0`

### 2. Replay the same edits through CLI/editor-core

The recorded replay used:

- first changelog:
  - `C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\web_export_latest\Stock---Copy-edited.changes.json`
- second changelog:
  - `C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\web_export_latest_v2\Stock---Copy-edited-edited.changes.json`

The first log contains the bulk `60` edits.
The second log contains the final `3` missed antilag edits.

Replay command:

```powershell
@'
from pathlib import Path
import json, base64
from editor_core import EditSession

root = Path(r'C:\Users\Michael Krymski\Documents\TunerPro Converter')
xdf_path = Path(r'C:\Users\Michael Krymski\Documents\N55Maps\Tools\000021571DAA01.xdf')
base_bin_path = Path(r'C:\Users\Michael Krymski\Documents\N55Maps\Tools\Stock - Copy.bin')
log1 = Path(r'C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\web_export_latest\Stock---Copy-edited.changes.json')
log2 = Path(r'C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\web_export_latest_v2\Stock---Copy-edited-edited.changes.json')
out_bin = root / 'tmp_compare' / 'cli_replay_E85v2.bin'

payload = {
    'source_xdf_b64': base64.b64encode(xdf_path.read_bytes()).decode('ascii'),
    'source_bin_b64': base64.b64encode(base_bin_path.read_bytes()).decode('ascii'),
    'source_xdf_name': xdf_path.name,
    'source_bin_name': base_bin_path.name,
    'pending_edits': json.loads(log1.read_text()).get('pending_edits', []) + json.loads(log2.read_text()).get('pending_edits', []),
}

session = EditSession.from_payload(payload)
result = session.export_bin_and_log(out_bin)
print(result['bin_path'])
print(result['log_path'])
print('pending_edits', len(payload['pending_edits']))
'@ | .\.venv\Scripts\python.exe -
```

Expected:

- replayed `63` edits total
- output BIN:
  - `C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\cli_replay_E85v2.bin`

### 3. Compare CLI replay vs TunerPro

```powershell
@'
from pathlib import Path
import hashlib

cli_bin = Path(r'C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\cli_replay_E85v2.bin')
manual_bin = Path(r'C:\Users\Michael Krymski\Documents\N55Maps\E85v2.bin')

cb = cli_bin.read_bytes()
mb = manual_bin.read_bytes()

print('same_size', len(cb) == len(mb))
print('same_hash', hashlib.sha256(cb).hexdigest() == hashlib.sha256(mb).hexdigest())
print('sha256', hashlib.sha256(cb).hexdigest())
print('diff_count', sum(1 for a, b in zip(cb, mb) if a != b))
'@ | .\.venv\Scripts\python.exe -
```

Expected:

- `same_size True`
- `same_hash True`
- SHA-256 `495fea5a8ddb7e187d8cad8e9151c6e8023e2ae18d91e27a9ceb2113fb42dd01`
- `diff_count 0`

XDF-aware compare:

```powershell
.\.venv\Scripts\python.exe tuner_cli.py --json compare `
  'C:\Users\Michael Krymski\Documents\N55Maps\Tools\000021571DAA01.xdf' `
  'C:\Users\Michael Krymski\Documents\TunerPro Converter\tmp_compare\cli_replay_E85v2.bin' `
  'C:\Users\Michael Krymski\Documents\N55Maps\E85v2.bin'
```

Expected summary:

- `changed_table_count: 0`
- `raw_change_range_count: 0`

## Human Notes

- The first failed compare was caused by an unsaved TunerPro BIN.
- The second failed compare was caused by three antilag tables not being edited in the web export.
- Once the missing antilag edits were applied in the web UI, all three paths converged exactly.

## Why This Case Matters

This case validates:

- TunerPro parity for saved BIN output
- web UI export parity
- editor-core replay parity
- CLI/session replay parity
- correct handling of both ordinary table edits and the late-added antilag zeroing edits

If this regression ever stops matching, treat it as a high-priority break in editing/export fidelity.
