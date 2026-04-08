# Progress

## Current Objective
- Implement the editor MVP on top of the existing XDF/BIN viewer without regressing the read-only workflow.

## Active Branch / Workspace
- Real repo branch: `codex/editor-mvp`
- Working copy: `C:\Users\Michael Krymski\Documents\Playground\TunerPro Converter\TunerPro Converter`

## Completed
- Read-only viewer baseline preserved and tagged as `v0.1-readonly-baseline`.
- Workflow, hierarchy, flat list, family grouping, AI Context, and description overlay are already in place.
- Added shared `editor_core.py` session model with stable `table_id`, selection normalization, preview/apply operations, undo/reset, export bundle support, and BIN writeback.
- Added writeback helpers in `bin_reader.py` for resolved axis layout, cell addressing, raw range handling, and byte packing.
- Extended Flask with session-backed edit endpoints for preview, apply, undo, reset, diff, and export while keeping parse/debug intact.
- Added `tuner_cli.py` with `commands`, `parse`, `list`, `show`, `diff`, `apply`, `undo`, `reset`, and `export`, backed by the same core and explicit session files.
- Added draft toolbar, per-table edit panel, region selection, preview/apply wiring, and export controls in the web UI.
- Added heatmap-style value coloring for table cells so high values trend red and low values trend green.
- Strengthened selection and preview outlines so highlighted cells are clearly visible on top of the table colors.
- Added direct X/Y axis breakpoint editing from the table view for embedded breakpoint axes, using the same draft session and undo/reset flow.
- Validated CLI writeback against manual TunerPro edits using `Stock_TestCLI.bin` / `Stock_TestCLIManual.bin`, ending with an exact binary match for scalar `set`, table `add`, table `multiply`, and `interpolate-x`.
- Fixed integer-table writeback verification to use representable-value tolerance instead of a one-size-fits-all epsilon.
- Fixed CLI edit math to respect XDF `decimalpl` behavior like TunerPro, while limiting display-precision rounding to the edited region only.
- Fixed axis writeback to preserve untouched breakpoint raw bytes and verify only the indices that were actually edited.
- Validated `interpolate-y`, `interpolate-xy`, and direct X/Y axis edits against manual TunerPro results, isolating and resolving the remaining axis serialization mismatch.
- Fixed CLI session replay/export so session files containing `axis_x_set` and `axis_y_set` edits can round-trip cleanly through `tuner_cli.py`.
- Fixed the same-session parallel CLI race by adding session-file locking and atomic session saves, preventing last-writer-wins overwrites.`r`n- Added a first-class `axis` CLI command so agents can preview and apply full X/Y breakpoint edits directly from `tuner_cli.py` without relying on shared-core helper scripts.

## In Progress
- WIP: Final live browser pass against the Flask app once run under the interpreter/environment that has Flask available.

## Next Up
- Run the Flask app in the user's normal environment and sanity-check selection, preview, apply, undo/reset, and export in-browser.
- Consider adding a first-class CLI command for axis edits instead of relying only on the shared editor core or the web API.
- Consider adding row/column header selection shortcuts and richer preview overlays if the basic editor UX feels solid.

## Blockers / Questions
- The active `python` in this workspace does not have Flask installed, so I could not complete a live browser/API run from this environment alone.

## Validation Log
- 2026-03-17: Found the current app is still read-only internally; no session/writeback layer existed yet.
- 2026-03-17: `python -m py_compile app.py bin_reader.py editor_core.py table_ranker.py tuner_cli.py xdf_parser.py` passed in the workspace copy.
- 2026-03-17: `node --check static/app.js` passed after adding draft toolbar, selection, preview/apply, and export UI wiring.
- 2026-03-17: Real XDF/BIN session created successfully from `000021571DAA01.xdf` + `93mapMK.bin`.
- 2026-03-17: CLI preview/apply/export verified against `WGDC Base` with a bounded `add_constant` edit and exported companion change log.
- 2026-03-17: CLI undo/reset verified after session-file history reconstruction was added.
- 2026-03-17: Impossible writeback was blocked on an out-of-range target value, confirming guard rails are rejecting unrepresentable edits instead of silently writing bad bytes.
- 2026-03-17: Real `WGDC Base` breakpoint writeback verified for both X and Y axes through `apply_axis_edit`, with draft session counts incrementing correctly.
- 2026-03-17: Heatmap and stronger selection visuals added in the web renderer; `node --check static/app.js` still passed afterward.
- 2026-04-08: Reproduced and fixed a `WGDC P-Factor` CLI writeback mismatch by switching round-trip verification to storage-aware tolerance and honoring XDF `decimalpl` during edited-region math.
- 2026-04-08: Final comparison between `Stock_TestCLIManual.bin` and `Stock_TestCLI_CLI_v4.bin` returned `diff_count 0`, validating scalar `set`, table `add`, table `multiply`, and `interpolate-x` against TunerPro.
- 2026-04-08: Cumulative validation on top of the earlier gold-case set showed `interpolate-y`, `interpolate-xy`, and requested WGDC Base axis edits matched TunerPro at the targeted cells/indices.
- 2026-04-08: Isolated axis-only comparison against `Stock_TestCLI_CLIManual2.bin` exposed untouched breakpoint drift; fixed by writing only changed axis indices and preserving untouched raw axis bytes.
- 2026-04-08: Corrected isolated axis-only export matched manual WGDC Base breakpoint values end-to-end, confirming the axis serialization fix.
- 2026-04-08: `tuner_cli.py export` verified successfully for a session file containing axis edits after teaching session replay to handle `axis_x_set` / `axis_y_set`.
- 2026-04-08: Same-session parallel CLI mutation test preserved both edits after adding a session lock and atomic save path, confirming the last-writer-wins race was fixed.`r`n- 2026-04-08: `tuner_cli.py axis` preview/apply verified end-to-end for `WGDC Base` X-axis edits, with the resulting session diff showing a persisted `axis_x_set` operation.

