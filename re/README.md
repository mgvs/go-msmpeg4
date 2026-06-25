# `re/` — reverse-engineering / provenance tools

These scripts derive every MS-specific table in the decoder **black-box**, using ffmpeg
only as an encoder (controlled bitstreams) and a decoder (pixel oracle) — never reading
ffmpeg source. They are kept as the evidence that the shipped tables were *derived, not
copied*. Grouped by codec:

```
re/
  common/      shared harness + lib + data + notes
  v2/          MS-MPEG4 v2-specific reversal
  v3_intra/    v3 I-frame tables (DC, AC/RL, MCBPC / table_mb_intra)
  v3_pframe/   v3 P-frame tables (motion vectors, inter MB-type)
  wmv1/        WMV1 (scan + DC-scale; reuses the v3 VLC tables)
```

## `common/`
- `craft.sh` — controlled-content encoder harness (YUV → DIV3 bitstream).
- `extract.sh` — pulls first-frame bitstream + decoded YUV from real samples.
- `decoder_oracle.py`, `recon_loop.py`, `clean_recon.py`, `clean_decoder.py`,
  `harness.py`, `fitter.py` — decode-and-consume-exactly helpers / reconstruction lib.
- `data/` — the committed JSON table dumps the generators read.
- `NOTES.md` — the full derivation log (`NOTES_acpred_virtualdub.md` is an early note).

## Current black-box generators (regenerate the shipped tables 1:1)
- `v3_intra/gen_tcoef_go.py`  → `tcoef_table.go` (luma/chroma intra RL, 102/168)
- `v3_intra/gen_rl_blackbox.py` (+ `rl_oracle.py`) → `tcoef_tables_extra.go` (RL 0/1/2, 465)
- `v3_intra/gen_mcbpc_go.py` (+ `iframe_mcbpc.py`) → `mcbpc_table.go` (`table_mb_intra`, 64)
- `v3_intra/gen_dc_go.py` / `gen_dc_luma.py` / `gen_dc_chroma.py` → DC VLC tables
- `v3_pframe/gen_mv_blackbox.py` (+ `pframe_oracle.py`) → `pframe_mv_vlc.go` (MV, 2×1100)
- `v3_pframe/gen_mb_blackbox.py` (+ `pframe_mb_extract.py`, `pframe_mb_intra.py`)
  → `mbNonIntraVLC` in `pframe_vlc.go` (128)

The `gen_*_blackbox.py` generators read the per-run JSON dumps under `/tmp/…` produced by
their matching oracle scripts; `gen_tcoef_go.py` / `gen_mcbpc_go.py` read `common/data/`.

> The other scripts (`clean_*`, `collect_*`, `reverse_*`, `gapfill_*`, …) are historical
> reversal scratch kept for provenance; some reference files/layout from earlier sessions
> and are not all runnable as-is.
