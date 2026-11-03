# datapack-agent review notes

## Base-run store

- **Path:** `~/dpa-lanes/base_runs/<main sha8>/<deal>/<step>/`. The one exception is held-out deals (Titan, Weldfit, Integrity, Pave): their entries live in `<vault>/base_runs/<main sha8>/<step>/` and report counts only.
- **Steps:** `ingest`, `cleaning-report`, `build`, `verify`, `deck`, and `query-<name>` for a named query set. Each entry holds the runner's outputs plus `MANIFEST.json`:
  - `command`, `cwd` and `main_sha`;
  - `inputs`: path → sha256 for every raw file;
  - `started`, `ended`, `exit`;
  - `producer` (agent name).
- **Publishing:** build into `<step>.tmp-<agent>`, then `mv` it to `<step>`. If `<step>` already exists, delete your tmp and use theirs. Never edit an entry after publishing it.
- **Using an entry:** copy it into your scratch before running anything on it, since runners write into deal folders. Check that `MANIFEST.json` `main_sha` equals your base, and that the input hashes match your deal copy.
- **Reproduction check:** run one deal's base step yourself and compare its outputs with the entry. Parquet: the same cells. Workbook: the same Check rows and values. Any difference voids that sha's entries; report it to the orchestrator.
- **Cleanup:** entries for a main sha older than the last 3 merges may be deleted by the orchestrator.
