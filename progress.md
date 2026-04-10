# Progress

## Current Objective
- Refactor the tuning app into a persistent workspace with shared favorites/folders, inline table editing, and a separate compare flow.

## Active Branch / Workspace
- Real repo branch: `codex/editor-mvp`
- Working copy: `C:\Users\Michael Krymski\Documents\TunerPro Converter`

## Completed
- Read-only viewer baseline preserved and tagged as `v0.1-readonly-baseline`.
- Workflow, hierarchy, flat list, family grouping, AI Context, and description overlay are in place.
- Added shared `editor_core.py` session model with stable `table_id`, selection normalization, preview/apply operations, undo/reset, export bundle support, and BIN writeback.
- Added writeback helpers in `bin_reader.py` for resolved axis layout, cell addressing, raw range handling, and byte packing.
- Extended Flask with session-backed edit endpoints for preview, apply, undo, reset, diff, and export while keeping parse/debug intact.
- Added `tuner_cli.py` with `commands`, `parse`, `list`, `show`, `diff`, `apply`, `undo`, `reset`, `export`, and first-class `axis` editing backed by the same core and explicit session files.
- Added draft toolbar, per-table edit panel, region selection, preview/apply wiring, and export controls in the web UI.
- Added heatmap-style value coloring for table cells and stronger selection/preview outlines.
- Added direct X/Y axis breakpoint editing from the table view for embedded breakpoint axes.
- Validated CLI writeback against manual TunerPro edits using `Stock_TestCLI.bin`, including scalar `set`, table `add`, table `multiply`, `interpolate-x`, `interpolate-y`, `interpolate-xy`, and direct X/Y axis edits.
- Fixed integer-table writeback verification to use representable-value tolerance instead of a one-size-fits-all epsilon.
- Fixed CLI edit math to respect XDF `decimalpl` behavior like TunerPro while limiting display-precision rounding to the edited region only.
- Fixed axis writeback to preserve untouched breakpoint raw bytes and verify only the indices that were actually edited.
- Fixed CLI session replay/export so session files containing `axis_x_set` and `axis_y_set` edits round-trip cleanly through `tuner_cli.py`.
- Fixed the same-session parallel CLI race by adding session-file locking and atomic session saves.
- Redesigned the loaded-results experience into a compact map-first tuning workspace with a left rail, map tabs, category tabs, and a right inspector.
- Replaced the old upload/results split with a persistent shell that stays visible before and after file load, including in-place `Parse / Reload` file controls.
- Removed second-BIN upload from the main tuning page and added a dedicated `/compare` route plus `/api/compare` for read-only BIN-vs-BIN analysis.
- Added shared preferences storage backed by `viewer_preferences.json`, with stable `preference_key` / `family_key` metadata in parse payloads.
- Replaced hardcoded quick-start surfacing with persistent favorites/folder scaffolding in the workspace shell.
- Moved normal value editing into the open table card, added double-click inline cell editing, and narrowed the inspector toward metadata plus breakpoint editing.
- Reworked breakpoint editing toward a one-axis-at-a-time vertical list and fixed the main layout scroll/overflow traps that were clipping the inspector on larger tables.
- Added a quick-add folder modal with search and bulk checkbox assignment for tables and families.
- Added friendlier XDF format detection so binary/proprietary XDF files fail with a clear message instead of a raw XML parser traceback.
- Added a first-class CLI `compare` command for mapped table diffs plus raw byte ranges, and made folders act as a real browse filter in the workspace.
- Added AI-friendly CLI discovery commands and selectors: `describe`, `maps`, `families`, selector-based target resolution for `show`/`apply`/`axis`, and a chainable `batch` manifest runner.
- Added a recorded parity/regression document at `E85V2_PARITY_REGRESSION.md` covering the canonical TunerPro vs web vs CLI exact-match case.
- Added AI table import preview/apply support so pasted AI-generated tables can be compared side-by-side against the current draft before applying.
- Added compare-mode transfer controls so changed tables can copy compare-side values, or values plus axes, into the base draft and export directly from `/compare`.
- Improved export flow so the web UI can export a raw BIN directly while writing a centralized JSON change log under `Documents\\N55Maps\\viewer_export_logs`.
- Improved `Copy for AI` so highlighted selections export a tighter, more explanation-friendly region instead of always dumping the full table.
- Added direct single-cell editing in compare mode by double-clicking the base-side grid after a compare session is loaded.

## In Progress
- WIP: Polish web editing interactions for real-world tune entry, including persistent last-used operation, better single-cell editing, ctrl/shift selection behavior, stronger selection outlines, preview-in-grid rendering, and family-local map switching.
- WIP: Polish favorites/folder management so family-level starring, folder ordering, and richer assignment controls feel complete in the new shell.
- WIP: Deepen compare-mode presentation with richer XDF overlap context for raw ranges and more detailed changed-cell summaries.

## Next Up
- Add nearest-XDF context and folder/category grouping to CLI compare output so agent summaries are even more actionable.
- Expand CLI batch manifests with optional preview-only and export steps in one pass.
- Add family-level starring and easier folder assignment directly from family rows.
- Improve compare mode with nearest XDF definitions for unmapped raw ranges and a denser changed-table summary.
- Do a live browser pass on the persistent shell to tighten any remaining UI regressions around inline editing, selection behavior, preview rendering, and inspector/map synchronization.

## Blockers / Questions
- Family-level favorites and AI-driven folder assignment are not finished yet, so the current preferences flow is strongest at the table level.
- Compare mode currently surfaces mapped tables and raw byte ranges, but it does not yet annotate raw ranges with nearest XDF definitions.

## Validation Log
- 2026-03-17: `python -m py_compile app.py bin_reader.py editor_core.py table_ranker.py tuner_cli.py xdf_parser.py` passed in the workspace copy.
- 2026-03-17: `node --check static/app.js` passed after adding draft toolbar, selection, preview/apply, and export UI wiring.
- 2026-03-17: Real XDF/BIN session created successfully from `000021571DAA01.xdf` + `93mapMK.bin`.
- 2026-03-17: CLI preview/apply/export verified against `WGDC Base` with a bounded `add_constant` edit and exported companion change log.
- 2026-03-17: CLI undo/reset verified after session-file history reconstruction was added.
- 2026-03-17: Impossible writeback was blocked on an out-of-range target value, confirming guard rails reject unrepresentable edits instead of silently writing bad bytes.
- 2026-03-17: Real `WGDC Base` breakpoint writeback verified for both X and Y axes through `apply_axis_edit`, with draft session counts incrementing correctly.
- 2026-03-17: Heatmap and stronger selection visuals added in the web renderer; `node --check static/app.js` still passed afterward.
- 2026-04-08: Final comparison between `Stock_TestCLIManual.bin` and `Stock_TestCLI_CLI_v4.bin` returned `diff_count 0`, validating scalar `set`, table `add`, table `multiply`, and `interpolate-x` against TunerPro.
- 2026-04-08: Cumulative validation on top of the earlier gold-case set showed `interpolate-y`, `interpolate-xy`, and requested WGDC Base axis edits matched TunerPro at the targeted cells and indices.
- 2026-04-08: Isolated axis-only comparison exposed untouched breakpoint drift; fixed by writing only changed axis indices and preserving untouched raw axis bytes.
- 2026-04-08: `tuner_cli.py export` verified successfully for a session file containing axis edits after teaching session replay to handle `axis_x_set` and `axis_y_set`.
- 2026-04-08: Same-session parallel CLI mutation test preserved both edits after adding a session lock and atomic save path, confirming the last-writer-wins race was fixed.
- 2026-04-08: `tuner_cli.py axis` preview/apply verified end-to-end for `WGDC Base` X-axis edits, with the resulting session diff showing a persisted `axis_x_set` operation.
- 2026-04-08: Real Flask smoke checks passed for the redesigned workspace: `GET /` rendered the new shell, `node --check static/app.js` passed, and `/api/parse` returned tables plus a live session for `000021571DAA01.xdf` + `Stock_TestCLI.bin`.
- 2026-04-09: `node --check static/app.js` passed after the persistent-shell, favorites, and inline-editor refactor.
- 2026-04-09: `python -m py_compile app.py preferences_store.py table_ranker.py editor_core.py` passed after adding preferences APIs and compare route support.
- 2026-04-09: Flask test-client smoke checks returned `200` for both `/` and `/compare` with the new templates.
- 2026-04-09: Real parse smoke test against `000021571DAA01.xdf` + `Stock_TestCLI.bin` returned `200`, created a live session, and included `preferences`, `preference_key`, and `family_key` in the payload.
- 2026-04-09: Real compare smoke test against `000021571DAA01.xdf`, `Stock_TestCLI.bin`, and `Stock_TestCLIManual.bin` returned `200` with mapped changed tables plus raw diff ranges.
- 2026-04-09: `node --check static/app.js` passed after adding quick-add folder modal wiring.
- 2026-04-09: Flask test-client `GET /` confirmed the quick-add modal markup is present in the rendered workspace shell.
- 2026-04-09: Binary/proprietary XDF detection verified against `Oil_Pressure_Tables_000021571DAA01_DEV.xdf`; parser now returns a clean user-facing format error and `/api/parse` returns `400`.
- 2026-04-09: `tuner_cli.py compare` verified against `000021571DAA01.xdf`, `Stock_TestCLI.bin`, and `Stock_TestCLIManual.bin`, returning mapped changed tables plus raw diff ranges.
- 2026-04-09: Flask test-client `GET /` confirmed the folder filter controls are present in the workspace shell.
- 2026-04-09: Byte-for-byte compare between `Stock---Copy-edited-edited.bin` (web export) and `E85v2.bin` returned `diff_count 0`, matching on raw bytes, SHA-256, mapped tables, and raw ranges.
- 2026-04-09: Replayed the full `63`-edit web changelog set onto `Stock - Copy.bin` through `editor_core.py` / CLI session replay and exported `cli_replay_E85v2.bin`; byte-for-byte compare against `E85v2.bin` returned `diff_count 0`.
- 2026-04-09: `tuner_cli.py --json describe`, `maps`, and `families` verified against a real session, returning command schemas, detected maps, and family/member listings suitable for agent discovery.
- 2026-04-09: Selector-based table resolution verified by resolving `Fuel (Main)` + `Map 2` to the primary table instead of axis companions.
- 2026-04-09: `tuner_cli.py batch` verified with a JSON manifest applying a selector-based edit step, then the session was reset cleanly afterward.
- 2026-04-09: AI table import smoke tests passed through Flask test-client for both scalar and multi-cell tables using `/api/session/<id>/import-preview` and `/api/session/<id>/import-apply` against `000021571DAA01.xdf` + `Stock_TestCLI.bin`.
- 2026-04-09: Compare-mode transfer smoke test passed through Flask test-client: `/api/compare` returned a live session id, `/api/session/<id>/transfer-reference` copied a changed editable table into the base draft, and `/api/session/<id>/export` returned a ZIP bundle successfully.
- 2026-04-09: Raw BIN export smoke test passed through Flask test-client: `/api/session/<id>/export-bin` returned `application/octet-stream` and included an `X-Export-Log-Path` header for the centrally saved changelog.
- 2026-04-09: Compare-mode direct cell edit smoke test passed by calling `/api/session/<id>/apply` against a compare-backed session and then exporting the edited BIN successfully.
