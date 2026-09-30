# Progress

## Agent Handoff Snapshot
- Branch: main
- Current stable state: editor workspace includes CSV log overlay and the new read-only AI inspection CLI
- Last pushed commit: 442fc84 Add read-only AI tune inspection CLI
- Do not commit: .claude/settings.local.json
- Ignore scratch files: tmp_compare/
- Key validation: E85v2 parity regression passed for Web, CLI, and TunerPro
- Next safest task: give a fresh AI agent the inspection guide and confirm it can navigate a tune without additional project history

## Current Objective
- Provide a discoverable, read-only CLI workflow for AI-assisted inspection of complete XDF/BIN tunes and cross-map table variants.

## Active Branch / Workspace
- Real repo branch: `main`
- Working copy: `C:\Users\Michael Krymski\Documents\TunerPro Converter`

## Completed
- 2026-04-26: Replaced one-off `Map 1 (Inferred)` title guessing with family-topology analysis in [table_ranker.py](C:/Users/Michael%20Krymski/Documents/TunerPro%20Converter/table_ranker.py). Metadata is now computed in batch so sibling presence can distinguish `global_shared`, `implicit_map1_with_explicit_others`, and `banked_map1_collapses_to_main`. Verified real XDF behavior for `Fuel (Main) (Bank 1/2)` -> canonical `Fuel (Main)` on `Map 1`, with `Fuel (Main) (Map 2/3/4)` as explicit siblings.
- 2026-04-26: Added CSV log overlay mode to the main workspace. The app can now load a single log CSV into the current session, scrub/play through samples, overlay active-cell and dwell-time data on the open table, persist per-family axis mappings and graph toggles in preferences, and show a docked right-side graph panel of selected traces.
- 2026-09-30: Added and pushed a separate read-only `inspect` CLI workspace to `main` (`442fc84`) with source fingerprints, complete categorized table catalog, on-demand table values, multi-map family comparison, and model-neutral onboarding documentation in `docs/AI_READ_ONLY_INSPECTION.md`.
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
- Added manual draft save/restore for the web UI by snapshotting full backend session payloads and recreating them cleanly from the browser.
- Improved map switching so changing Map 1/2/3/4 tries to stay on the sibling table in the same family instead of dropping you back into the broad hierarchy position.
- Fixed AI import diff rendering to compare the full current/import grids and axes instead of relying on the truncated change list for highlighting.
- Fixed AI import with axes so applying the import clears stale cached axis drafts in the UI and shows the refreshed breakpoint values immediately.
- Added a `Mirror to sibling maps` toggle in the inline editor so normal table edits, axis edits, and AI imports only fan out across sibling map variants when explicitly enabled.

## In Progress
- WIP: Polish web editing interactions for real-world tune entry, including persistent last-used operation, better single-cell editing, ctrl/shift selection behavior, stronger selection outlines, preview-in-grid rendering, and family-local map switching.
- WIP: Polish favorites/folder management so family-level starring, folder ordering, and richer assignment controls feel complete in the new shell.
- WIP: Deepen compare-mode presentation with richer XDF overlap context for raw ranges and more detailed changed-cell summaries.
- WIP: Do a live browser pass on the CSV log overlay UX to tune mapping defaults, playback ergonomics, and chart density against real logs.

## Next Up
- Validate AI inspection on a fresh agent runtime and gather feedback on category naming and any missing per-table metadata.
- Validate the CSV log overlay in the browser on several representative tables and tighten any mapping heuristics that still need manual overrides.
- Consider adding a persisted log-session restore path if keeping a loaded CSV across draft restores becomes important in practice.
- Add nearest-XDF context and folder/category grouping to CLI compare output so agent summaries are even more actionable.
- Expand CLI batch manifests with optional preview-only and export steps in one pass.
- Add family-level starring and easier folder assignment directly from family rows.
- Improve compare mode with nearest XDF definitions for unmapped raw ranges and a denser changed-table summary.
- Do a live browser pass on the persistent shell to tighten any remaining UI regressions around inline editing, selection behavior, preview rendering, and inspector/map synchronization.

## Blockers / Questions
- Family-level favorites and AI-driven folder assignment are not finished yet, so the current preferences flow is strongest at the table level.
- Compare mode currently surfaces mapped tables and raw byte ranges, but it does not yet annotate raw ranges with nearest XDF definitions.
- CSV logs are kept in memory only for the active browser session in v1; they are not restored from disk automatically after a page refresh or app restart.

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
- 2026-04-09: Draft snapshot/restore smoke test passed through Flask test-client: `/api/session/<id>/snapshot` returned a full payload and `/api/session/restore` recreated a live session with tables intact.
- 2026-04-09: AI import with `values_and_axes` smoke test passed through Flask test-client, and the refreshed table payload returned updated X and Y axis labels immediately after apply.
- 2026-04-10: `node --check static/app.js` passed after adding the explicit mirror-toggle workflow for edits/imports/axis changes; the fan-out path is wired through the shared apply handlers and is ready for live UI validation against real multi-map families.
- 2026-04-26: `python -m py_compile app.py preferences_store.py log_overlay.py` passed after adding log-session parsing, overlay computation, and preference persistence for axis mappings/graph toggles.
- 2026-04-26: `node --check static/app.js` passed after adding the workspace log bar, table overlay rendering, graph panel wiring, and log-aware refresh paths.
- 2026-04-26: Flask test-client log smoke tests passed against `000021571DAA01.xdf`, `Stock - Copy.bin`, and `2026-04-19 00_46_02 000021571DAA01 E85v20ThrottleAggro.bin.csv`:
- 2026-04-26: `/api/log/parse` returned `200` with `sample_count=106`, parsed metadata, time range, and discoverable columns.
- 2026-04-26: `/api/preferences` round-trip persisted `log.playback_speed`, `log.graph.enabled_columns`, and `log.axis_mappings`.
- 2026-04-26: `/api/log/overlay` returned `200` for `Timing (Main) (Map 3)` with explicit mapping `Load actual RAM` + `RPM (rpm)` + `Current Map (-)`, producing `mapped_sample_count=106`, `unmapped_sample_count=0`, a live `active_cell`, and graph series for `RPM (rpm)`, `Load actual RAM`, and `Throttle Position (*)`.
- 2026-09-30: Read-only inspector unit tests passed for complete catalog/show output, banked Map 1 versus Maps 2–3 family comparisons, source immutability, stale-input rejection, and axis-mismatch delta suppression.
- 2026-09-30: Full test suite passed (12 tests); read-only inspection CLI commit `442fc84` was fast-forward-pushed to GitHub `main`, and this laptop's local `main` was fast-forwarded to match.
- 2026-09-30: Real CLI smoke against `000021571DAA01.xdf` + `Stock - Copy.bin` opened a workspace with 1,115 definitions; Map 2 Fueling catalog returned 28 entries; `Fuel (Main)` comparison returned five primary variants and six bank-to-map comparison pairs; Map 2 table selection resolved to the main 17x18 table; input SHA-256 hashes were unchanged.
