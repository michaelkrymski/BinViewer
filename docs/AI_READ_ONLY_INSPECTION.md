# AI Read-Only Tune Inspection

This guide describes a read-only way to inspect every XDF-defined table in a BIN. It is intended for any AI model or human that can run the repository's Python CLI and access the supplied files.

The inspection commands only read the XDF and BIN. `inspect open` creates a small workspace manifest containing their paths, sizes, and SHA-256 fingerprints. It does not copy or modify the tune. Later commands check those fingerprints and stop if either source file has changed. Reopen the workspace after intentionally replacing either input.

The inspection command group does not provide edit, undo, reset, or export operations. The separate top-level editing commands still exist; do not use them for read-only review.

## Start Here

Run these commands from the repository root in PowerShell. Use `py` or the project's virtual-environment Python as appropriate.

```powershell
python tuner_cli.py inspect open "C:\path\to\calibration.xdf" "C:\path\to\calibration.bin" --workspace "C:\path\to\calibration.inspect.json"
python tuner_cli.py inspect guide
python tuner_cli.py --json inspect maps --workspace "C:\path\to\calibration.inspect.json"
python tuner_cli.py --json inspect catalog --workspace "C:\path\to\calibration.inspect.json"
```

Use `python tuner_cli.py inspect --help` and `python tuner_cli.py inspect <command> --help` to discover syntax. Put the global `--json` option before `inspect`.

## Browse the Tune

`maps` summarizes map-specific table and family counts, category counts, and shared definitions. `categories` lists both the app's workflow buckets and the source XDF category paths.

`catalog` returns compact metadata for every matching definition, without the full table grids. Filter it to reduce output:

```powershell
python tuner_cli.py --json inspect catalog --workspace tune.inspect.json --map "Map 2" --bucket "Fueling"
python tuner_cli.py --json inspect catalog --workspace tune.inspect.json --family "Fuel (Main)"
python tuner_cli.py --json inspect search --workspace tune.inspect.json "lambda" --map "Map 1"
python tuner_cli.py --json inspect families --workspace tune.inspect.json --search "WGDC"
```

Catalog entries include `table_id`, title, workflow category, original XDF category path, family key/title, map assignment and inference reason, map topology, subrole, axis-companion role/suffix when present, shape, units, axis labels/units/count/range/endpoints, descriptions, raw XDF data address when defined, element size, autogen status, and read errors. Axis companion definitions remain individually searchable and inspectable in the catalog.

Use `show` to retrieve one table's complete axes and values. Choose one selector; use `--map` to disambiguate a family with multiple members in one map:

```powershell
python tuner_cli.py --json inspect show --workspace tune.inspect.json --family "Fuel (Main)" --map "Map 2"
python tuner_cli.py --json inspect show --workspace tune.inspect.json --title "WGDC Base (Custom)" --map "Global"
```

The JSON `show` response contains `workspace`, `table`, and `formatted`. `table.values` is row-major; X-axis values correspond to columns and Y-axis values correspond to rows. Scalar definitions are returned as a one-cell grid.

## Compare Map Variants

Use `compare-maps` with a family title or key. By default it compares against Map 1, or the first map present if Map 1 is absent. `--against-map` selects another baseline.

```powershell
python tuner_cli.py --json inspect compare-maps --workspace tune.inspect.json --family "Fuel (Main)"
python tuner_cli.py --json inspect compare-maps --workspace tune.inspect.json --family "Timing (Main)" --against-map "Map 2"
```

The response includes the primary map-specific table variants with complete axes and values, plus references to axis companion definitions. It also includes comparisons between each baseline member and each member of every other map. This covers families where Map 1 has Bank 1 and Bank 2 tables while later maps have a single Main table; each pair is labeled by its actual title and subrole.

Cell deltas are calculated only when row/column dimensions and both axes match exactly. For other pairs, `status` is `not_aligned`, `alignment_issues` describes the shape or axis mismatch, and no cell deltas are returned. This avoids implying that equal cell positions represent equal operating points when their axes differ.

## Output and Interpretation

Use `--json` for model/tool automation. JSON outputs are structured and include workspace identity; catalog and family results report match counts and truncation. Text output is intended for quick terminal review.

Map labels may be explicit, inferred from family topology, `Global`, or `Unassigned`. Check `map_inferred`, `map_assignment_reason`, and `map_topology` before treating an inferred assignment as certain. Descriptions combine XDF text and the app's curated metadata overlay; missing descriptions mean the project has no known explanation for that definition.

This interface supplies tune data and comparisons for an AI to analyze. It does not establish whether a calibration is safe or diagnose mechanical faults automatically. A model can only use the CLI when its host gives it terminal execution and access to the input files.
