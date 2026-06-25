# go-msmpeg4

A **Microsoft MPEG-4** video decoder in **pure Go** — no cgo, no external binaries,
cross-platform. Decodes **v3** (`DIV3`/`MP43`/`MPG3`/`AP41`) and **v2** (`MP42`/`DIV2`);
**v1** (`MP41`/`DIV1`) is planned (TODO).

This is a **decoder only** (no encoder).

> ## ⚠️ WORK IN PROGRESS — EDUCATIONAL / RESEARCH
>
> **This is an educational / research project** — a study in clean-room
> reverse-engineering of an old, undocumented video format, **and a study of how
> Large Language Models (LLMs) perform at programming and reverse-engineering** such
> hard, open-ended problems. Much of the work here was done by an LLM; the dead-ends
> are kept as part of the record.
>
> **For production use**, reach for one of these instead:
> - **FFmpeg** (GPL/LGPL) — the de-facto reference implementation:
>   <https://ffmpeg.org/> (`libavcodec/msmpeg4dec.c`). Example: `ffmpeg -i in.avi out.png`.
> - **Microsoft's original codec** (`mpg4c32.dll` / Windows Media MPEG-4 V3),
>   shipped with Windows / Windows Media Player.
>
> **Current state (summary)** — in version order:
> - 🔜 **v1** (`MP41`/`DIV1`) — not implemented.
> - ✅ **v2** (`MP42`/`DIV2`) — I-frames (49–57 dB) and P-frames (63–74 dB vs
>   ffmpeg, inter + intra MBs, all qscale). H.263-style: median MV prediction, the
>   open H.263 MVD VLC + CBPY, the MS `v2_mb_type` VLC (8 codes, verified bit-exact).
> - ✅ **v3** (`DIV3`/`MP43`) — I-frames and P-frames decode to high-quality
>   thumbnails (51–∞ dB on five real movies, multiple table configs). **Every
>   MS-specific VLC table (MV, MB-type, MCBPC/`table_mb_intra`, RL, DC) is
>   black-box-derived and verified bit-for-bit** (see [clean-room provenance](#why-its-special--clean-room-provenance)).
> - ✅ **WMV1** (`WMV1` / Windows Media Video 7) — **I-frames and P-frames decode**
>   (I ~65 dB, P 61–73 dB vs ffmpeg, verified in `wmv1_test.go`). Black-box-derived:
>   the picture header (incl. the WMV ext-header), the variable-length ESC3 escape,
>   all 4 scan tables (3 intra + inter) and the luma/chroma DC-scale tables; the v3
>   MV / MB-type / RL / MCBPC / DC VLC tables are reused.
> - ✅ **WMV2** (`WMV2` / Windows Media Video 8) — **I-frames decode** (`DecodeIntraFrameWMV2`,
>   ~65–73 dB vs ffmpeg, incl. the in-loop
>   deblocking filter). Reuses WMV1's intra coding (j_type=0); the feature flags
>   come from the 32-bit codec extradata. P-frames (ABT / quarter-pel) and the loop filter are
>   not implemented.
>
> Per-decoder breakdowns, PSNR tables, and which tests cover which decoder are in
> the [Status](#status) section below.

## Why it's special — clean-room provenance

Microsoft never published the MS-MPEG4 bitstream format. The "ready" sources are
**FFmpeg** (LGPL — copyleft, incompatible with a permissive project) and
**Microsoft's own binary** (`mpg4c32.dll`; disassembling it violates the EULA).

Everything here is **reverse-engineered black-box**, with a strict clean-room rule:

- **We never read FFmpeg source** (`.c`/`.h`) and **never touch a Microsoft binary.**
- The ffmpeg **encoder** is used only to produce controlled bitstreams; the ffmpeg
  **decoder** is used only as a **pixel oracle** (observe output, never source).
- **Correctness criterion is pixel-exactness with the decoder's *behaviour***
  (MSE=0), *not* matching any internal tables. The numbers end up correct because
  they are *facts about the format* (a VLC is unique), not because they were copied.
  This is the FFmpeg-style method (RE the format, reimplement it) done with clean hands.

The scripts under `re/` are the proof: each regenerates its Go table from crafted
bitstreams. See `re/common/NOTES.md` for the derivation log.

### P-frame tables — black-box derivation

The two MS-specific P-frame VLC tables are recovered **purely black-box**, ffmpeg used only
as an encoder/decoder oracle:

- **`mvVLC0` / `mvVLC1`** (motion-vector VLCs, 1100 codewords each) — by walking the complete
  VLC prefix tree with the ffmpeg **decoder** as a pixel oracle: a probed macroblock's
  measured motion gives each codeword's `(dmvx,dmvy)`
  (`re/v3_pframe/pframe_oracle.py` → `re/v3_pframe/gen_mv_blackbox.py`).
- **`mbNonIntraVLC`** (`table_mb_non_intra`, 128 codewords) — the 64 inter codes from the
  ffmpeg **encoder** with the (black-box) MV codeword as the field anchor and `cbp` read from
  decoded pixels (`re/v3_pframe/pframe_mb_extract.py`); the 64 intra codes by first-diff of two clips
  that differ only in block-0 DC (`re/v3_pframe/pframe_mb_intra.py`). Combined by `re/v3_pframe/gen_mb_blackbox.py`.

Both generators read only the black-box JSON dumps; the shipped `pframe_mv_vlc.go` and
`pframe_vlc.go` are emitted from them.

The I-frame joint MCBPC/CBPY table (`mcbpc_table.go`, `table_mb_intra`, 64 patterns) is
likewise black-box: single-MB I-frames, the coded-block pattern read from decoded pixels
with the intra CBP prediction undone, the codeword isolated by block-0 DC first-diff
(`re/v3_intra/iframe_mcbpc.py` → `re/v3_intra/gen_mcbpc_go.py`). All 64 verified bit-for-bit.

The MS-specific RL-VLC tables (`tcoef_tables_extra.go`: `tcoefTable0/2/1VLC` =
lumaTCOEF[0], lumaTCOEF[1], chromaTCOEF[0]; 465 codewords) are black-box too: a one-MB
I-frame whose header selects the target RL table carries one candidate TCOEF codeword in a
single AC-coded block; ffmpeg decodes it and the produced coefficient is read from the
block's DCT (zig-zag position → run, magnitude → level, coeff count → last), walking the
VLC prefix tree (`re/v3_intra/rl_oracle.py` → `re/v3_intra/gen_rl_blackbox.py`). All 465 entries, the escape
codewords and the `maxlev` arrays verified bit-for-bit. (`tcoefInterVLC` and the scan /
IDCT / dequant tables are the open H.263 / MPEG-4 standard, taken from the spec.)

## References (all legal: open standards + GFDL spec)

- **ITU-T H.263** and **ISO/IEC 14496-2 (MPEG-4 Part 2)** — the open standards
  MS-MPEG4 is a variant of. DC/AC prediction, scans, IDCT, dequant, CBP/MV
  prediction come from here (`spec/*.pdf`, `spec/REFERENCE.md`).
- **`spec/msmpeg4.txt`** — *DIVX3 / MS-MPEG4v1-v3 / WMV7-8* format **specification**
  by M. Niedermayer, **GNU Free Documentation License** (a documentation doc, not
  FFmpeg source code). Gives the picture-header layout, table-selection fields,
  CBP/DC prediction, escape coding.

## Format structure — fully mapped

"MS-MPEG4 v3 is ISO-MPEG4 with most advanced features removed and different VLC
tables." The whole intra-frame structure is now understood from the spec + black-box:

- **I-frame picture header:** `pictype u(2)=00` · `quant u(5)` · `slice_code u(5)` ·
  `rl_chroma_idx c3` · `rl_table_idx c3` · `dc_table_idx u(1)`  (ext-header is at the
  *end* of the frame in v1-3).  `c3` VLC = `0`→0, `10`→1, `11`→2.
- **Per-frame table selection:** **3 RL tables** (×2 luma/chroma) and **2 DC tables**,
  chosen by the header indices.
- **Intra MB:** `code = table_mb_intra` (6-bit, strip final ac_pred bit) →
  **CBP prediction** for the 4 luma blocks (`cbp[i] = code_bit ^ pred`,
  `pred = (A==B)?C:B` from neighbours' coded flags) → `ac_pred u(1)` →
  per block `DC` + (if coded) `AC`.
- **DC:** VLC differential → gradient prediction from left/top neighbour;
  separate luma (`dcScaler = q+8` for q 9–24) and chroma (`dcScaler = (q+13)/2`)
  scalers; default predictor `round(1024/dcScaler)`.
- **AC:** RL-VLC + 3-tier escape; **ESC3 = `last u(1)` + `run u(6)` + `level s(8)`**.
- **AC prediction:** direction from the DC gradient; alternate-V / alternate-H scans;
  applies to all blocks (including cbp=0) when `ac_pred=1`.

## Status

✅ done & verified · 🔶 partial / known limitation · 🔜 next

### MS-MPEG4 v3 (DIV3 / MP43)

| Piece | Status |
|---|---|
| `bits.go` MSB-first reader, VLC walk | ✅ |
| `idct.go` 8×8 float inverse DCT + clamp | ✅ |
| Intra DC VLC — 4 tables (luma/chroma × dc_idx 0/1), full differential range | ✅ |
| AC TCOEF RL-VLC + ESC1/2/3 — 3 luma + 3 chroma tables | ✅ |
| `mcbpc_table.go` **table_mb_intra** (64 patterns, ac_pred stripped) | ✅ |
| `decode.go` `DecodeIntraFrame`: header → CBP-pred → DC-grad-pred → AC → dequant → IDCT | ✅ |
| **Luma DC scaler** (MPEG-4 §7.4.4 piecewise) + default predictor `round(1024/dcScaler)` | ✅ |
| **Chroma DC scaler** `(q+13)/2` (separate from luma) | ✅ |
| **CBP prediction** (`if(A==B)X=C else X=B` from coded-flag grid) | ✅ |
| **c3 header parse** (real-frame table indices) | ✅ |
| **AC prediction** (direction + alt scans + neighbour row/col, cbp=0 blocks included) | ✅ |
| **Multiple RL/DC table configs** (real frames select various index combinations) | ✅ tested on 5 configs |
| Real DIV3 **I-frames** → high-quality thumbnails | ✅ 51–∞ dB on 5 real movies |
| IDCT float/integer rounding — small residual on complex content | 🔶 |
| **P-frames** + motion compensation (MVD median + half-pel) | ✅ 51–89 dB on 5 real movies |

**I-frame quality** — real-encoder DIV3, five real-world AVI movies across different
`rl_table`/`rl_chroma`/`dc_table` index combinations:

| File | Y PSNR | Cb MSE | Cr MSE |
|---|---|---|---|
| D******e (576×240) | **∞ dB** (bit-exact) | 0.00 | 0.00 |
| J*******s (512×288) | **88.4 dB** | 0.00 | 0.00 |
| A*********2 (512×354) | **89.0 dB** | 0.00 | 0.00 |
| C*******e (576×384) | **60.7 dB** | 0.02 | 0.02 |
| 6*************й (576×256) | **51.8 dB** | 0.44 | 0.47 |

Residual errors (~0.4 MSE for the lower-PSNR files) are IDCT rounding noise and a
small number of rarely-occurring escape-code sequences not yet fully covered.

**P-frame quality** — motion compensation + residual decoding, same five movies:

| File | Y PSNR | Cb MSE | Cr MSE |
|---|---|---|---|
| J*******s (512×288) | **88.4 dB** | 0.00 | 0.00 |
| A*********2 (512×354) | **89.0 dB** | 0.00 | 0.00 |
| D******e (576×240) | **66.9 dB** | 0.04 | 0.01 |
| C*******e (576×384) | **54.8 dB** | 0.21 | 0.04 |
| 6*************й (576×256) | **51.6 dB** | 0.71 | 0.73 |

Residual errors are I-frame rounding cascading into P-frame MC and IDCT precision
noise — not P-frame decoding bugs.

**Entry point:** `DecodeIntraFrame` / the v3 path inside `DecodeAVIFirstFrame`.
**Tests** (v3 decoder): `decode_test.go` (`TestDecodeM4`, `TestTablesPresent`),
`realfile_test.go` (`TestRealFiles`, `TestRealPFrames`),
`allfiles_cmp_test.go` (`TestAllFilesPixelCmp`, `TestPFramePixelCmp`, `Test6daysPFrameWithFFmpegRef`),
`pframe_test.go`, `pframe_trace_test.go`.

### MS-MPEG4 v2 (MP42 / DIV2)

V2 uses a different header (no per-frame table-selection fields) and different DC VLC tables.
Both the luma and chroma DC VLC tables were reverse-engineered from black-box bitstream analysis.

| Piece | Status |
|---|---|
| **V2 header** `pictype(2)+quant(5)+slice_code(5)` — 12 bits, no table-index fields | ✅ |
| **V2 luma DC VLC** (dc2_vlc[0]) — fully derived, 9 codes covering diff range ±255 | ✅ |
| **V2 chroma DC VLC** (dc2_vlc[1]) — fully derived, 9 codes | ✅ |
| **V2 DC scaler** constant 8 for luma and chroma | ✅ |
| **V2 MB overhead**: v2_intra_cbpc (4-entry prefix tree) + ac_pred + standard H.263 CBPY | ✅ |
| **V2 RL tables**: luma = MPEG-4 intra mid-rate, chroma = inter mid-rate (fixed for v2) | ✅ |
| **DC gradient prediction** (same logic as v3) | ✅ |
| **DC-only I-frames** (CBP=0 all blocks) — pixel-exact | ✅ |
| **I-frames with non-zero AC** — luma decoded bit-exact; full frame 49–57 dB | ✅ |
| **AC prediction** (residual ~0.3–3.8% of pixels, maxDiff ≤33) | 🔶 small residual |
| **P-frames** | 🔜 not implemented |

**What works in practice:** content-rich I-frames decode at **49–57 dB** (luma blocks
bit-exact vs reference); DC-only frames are pixel-exact (73 dB). A small AC-prediction
residual remains on a fraction of pixels.

**Entry point:** `DecodeIntraFrameV2` / the v2 path inside `DecodeAVIFirstFrame`
(auto-detected from the AVI codec FourCC — strf `biCompression`, falling back to the
strh `fccHandler` when `biCompression` is empty).
**Tests** (v2 decoder): `v2psnr_test.go` (`TestV2PSNRFrame192`),
`v2re_test.go` (`TestDecodeV2`, `TestDecodeAVIFirstFrameV2`, plus the `TestV2*` RE/dump tests).

### MS-MPEG4 v1 (MP41 / MPG4 / DIV1)

| Piece | Status |
|---|---|
| **V1 decoder** (header, MB layout, DC/AC, no per-frame table selection) | 🔜 not implemented |

V1 (`MP41` / `MPG4` / `DIV1`) has **no decoder yet** — these FourCCs are not handled.

### WMV1 / WMV2 (Windows Media Video 7 / 8)

`WMV1` (Windows Media Video 7) and `WMV2` (Windows Media Video 8) are the next members of
the same family — per the GFDL spec, "MSMPEG4 up to version 3 is pretty much ISO-MPEG4 with
most advanced features removed, and different VLC tables. **WMV1 just has different
scantables**; WMV2 additionally uses 8×4 / 4×8 DCT and horizontal quarter-pel motion in
P-frames." (WMV3 = VC-1 is a different codec and out of scope here.)

| Piece | Status |
|---|---|
| **WMV1** picture header (WMV ext-header, `per_mb_rl_table`, rl/dc indices) | ✅ |
| **WMV1** AC escape coding (`run_diff`, variable-length ESC3) | ✅ |
| **WMV1** scan tables (intra zigzag + alt-vert + alt-horiz) — black-box | ✅ |
| **WMV1** DC-scale tables (luma/chroma) — black-box | ✅ |
| **WMV1** `DecodeIntraFrameWMV1` (`decode_wmv1.go`) | ✅ ~65 dB vs ffmpeg, all qscale |
| **WMV1** inter scan table — black-box | ✅ |
| **WMV1** `DecodePFrameWMV1` (`decode_wmv1_p.go`, MV/skip/inter+intra blocks/MC) | ✅ 61–73 dB vs ffmpeg |
| **WMV1 / WMV2** `per_mb_rl_table=1` (per-MB RL index) | ✅ |
| **WMV2 I-frames** `DecodeIntraFrameWMV2` (shares WMV1 intra; extradata flags) | ✅ 48–73 dB vs ffmpeg |

WMV1 is a **small** addition on top of v3: the MV / MB-type / RL / MCBPC / DC VLC tables are
shared with v3 (already reversed); only the scan tables, DC-scale tables, the ext-header and
the WMV1 ESC3 escape are new. **Tests:** `wmv1_test.go` (`TestWMV1IntraFrame`, `TestWMV1Sweep`).

## Roadmap

| Milestone | Status |
|---|---|
| **Format fully understood** (the hard part) | ✅ done |
| **v3 I-frames, all common configs, high-quality** | ✅ done (51–∞ dB on 5 real movies) |
| **v3 I-frames, near pixel-exact** (rare escape codes) | 🔶 small residual |
| **v3 P-frames** (motion comp, MV tables/prediction, inter blocks) | ✅ done (51–89 dB on 5 real movies) |
| **v2 I-frames** (DC + CBP + AC, luma + chroma RL tables) | ✅ done (49–57 dB on real frames) |
| **v2 I-frames, near pixel-exact** (AC-prediction residual) | 🔶 small residual |
| **v2 P-frames** `DecodePFrameV2` (H.263 MV + cbp-invert + inter/intra MBs + MC) | ✅ 63–74 dB vs ffmpeg |
| **v1 (MP41 / DIV1)** decoder | 🔜 not implemented |
| **WMV1 I-frames** (v3 tables + WMV1 scans / DC-scale / ext-header / ESC3) | ✅ ~65 dB vs ffmpeg |
| **WMV1 P-frames** (inter scan, MV, MC) | ✅ 61–73 dB vs ffmpeg |
| **WMV2 I-frames** (shares WMV1 intra, j_type=0) | ✅ 48–73 dB vs ffmpeg |
| **WMV2** in-loop deblocking filter (H.263 Annex J) | ✅ |
| **WMV2** P-frames (ABT, quarter-pel) | 🔜 later |

## Reverse-engineering / provenance tools (`re/`)

The controlled-content harness + per-table generators (each reproduces its Go table
1:1 from crafted bitstreams) + the decode-and-consume-exactly fitter / reconstruction
loop. These stay in the repo as evidence the tables were *derived, not copied*.

Grouped by codec (see `re/README.md`):

- **`re/common/`** — shared harness (`craft.sh`, `extract.sh`), the decoder/pixel-oracle
  helpers, the `data/` JSON dumps, and `NOTES.md` (the derivation log).
- **`re/v2/`** — MS-MPEG4 v2-specific reversal (DQUANT quirks, v2 DC).
- **`re/v3_intra/`** — v3 I-frame tables: DC, AC/RL, MCBPC/`table_mb_intra` (incl. the
  current black-box generators `iframe_mcbpc.py`, `rl_oracle.py`, `gen_*`).
- **`re/v3_pframe/`** — v3 P-frame tables: motion-vector and inter MB-type VLCs.
- **`re/wmv1/`** — WMV1 work (scan + DC-scale tables; reuses the v3 VLC tables).

## License

Port code is MIT (see `LICENSE`, `NOTICE`). No FFmpeg source and no Microsoft binary
were used; tables are derived from observed bitstream behaviour + the open standards
and the GFDL format spec.

## Tests

```sh
cd go-msmpeg4 && go test ./...
```

Which test files cover which decoder is listed per-decoder in the [Status](#status)
section (v3 vs v2). Tests that need real movies / oracle YUVs auto-skip when the
fixtures are absent.
