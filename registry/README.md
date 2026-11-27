# Durable experiment registry

Tamper-evident records that must survive the loss of the local store (`data/`) and results (`results/`), which are
git-ignored.

- `prospective/<experiment>.json`: the pending prospective predictions of an experiment. Each entry holds the stored
  test spec and its sha256, the real registration timestamp, the window classes, the frozen protocol and the criteria
  hash. The registry sha256 covers the whole file.
- `evidence/<sha256>.gz`: the raw evidence snapshots those predictions were made on, gzip-compressed and
  content-addressed. The decompressed bytes hash to the file name, which is also `snapshots.sha256` in the store and in
  the registry files.

Verify: `python scripts/export_prospective_registry.py <results/.../r2s...> --verify`.

**Source and licence.** `evidence/204fb505…gz` (DfT VEH0160): UK Department for Transport, vehicle licensing statistics,
`df_VEH0160_UK.csv`, used under the Open Government Licence v3.0. It contains public sector information licensed
under the OGL v3.0 (https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
