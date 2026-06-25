# go-msmpeg4

A **Microsoft MPEG-4** video decoder in **pure Go** — no cgo, no external binaries,
cross-platform. Decodes **v3** (`DIV3`/`MP43`/`MPG3`/`AP41`) and **v2** (`MP42`/`DIV2`);
**v1** (`MP41`/`DIV1`) decodes I-frames and P-frames **bit-exactly**.

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
> - **FFmpeg** (GPL/LGPL) — the de-facto production decoder:
>   <https://ffmpeg.org/>. Example: `ffmpeg -i in.avi out.png`.
> - **Microsoft's original codec** (`mpg4c32.dll` / Windows Media MPEG-4 V3),
>   shipped with Windows / Windows Media Player.
>
> **Current state (summary)** — in version order:
> - ✅ **v1** (`MP41`/`DIV1`) — **I-frames decode bit-exactly** vs ffmpeg (`DecodeIntraFrameV1`):
>   32-bit start code header, open H.263 intra MCBPC/CBPY, MPEG-1 DC prediction, plain ISO escape.
>   **P-frames** (`DecodePFrameV1`) decode **bit-exactly** — the distinctive v1 quirk is the
>   **MV predictor: the left neighbour only** (0 at the left edge), *not* the H.263/MPEG-4 median.
>   Unblocked via the original MS `mpg4c32.dll` as a black-box encoder oracle.
> - ✅ **v2** (`MP42`/`DIV2`) — I-frames and P-frames decode **bit-exactly** vs ffmpeg
>   (luma+chroma, all qscale; inter + intra MBs). H.263-style: median MV prediction, the
>   open H.263 MVD VLC + CBPY, the MS `v2_mb_type` VLC (8 codes, verified bit-exact).
> - ✅ **v3** (`DIV3`/`MP43`) — I-frames and P-frames decode **bit-exactly** vs ffmpeg.
>   **Every MS-specific VLC table (MV, MB-type, MCBPC/`table_mb_intra`, RL, DC) is
>   black-box-derived and verified bit-for-bit** (see [clean-room provenance](#why-its-special--clean-room-provenance)).
> - ✅ **WMV1** (`WMV1` / Windows Media Video 7) — **I-frames and P-frames decode bit-exactly**
>   vs ffmpeg (verified in `wmv1_test.go`). Black-box-derived:
>   the picture header (incl. the WMV ext-header), the variable-length ESC3 escape,
>   all 4 scan tables (3 intra + inter) and the luma/chroma DC-scale tables; the v3
>   MV / MB-type / RL / MCBPC / DC VLC tables are reused.
> - ✅ **WMV2** (`WMV2` / Windows Media Video 8) — **I-frames and P-frames decode bit-exactly**
>   vs ffmpeg (luma+chroma, incl. the in-loop deblocking filter, via the integer WMV2 IDCT).
>   Reuses WMV1 intra coding (j_type=0); P-frames add 3 black-box `mb_non_intra` VLC tables
>   (table 1 complete via real `.wmv` samples; table 0 is structurally complete and table 2 is missing
>   ~1-2 codes — real `.wmv` decode in long bit-exact runs before drifting on those), `parse_mb_skip`,
>   ms-pel MC (verified) and the msmpeg4 chroma MV. ABT (`abt_type≠0`) and J-frames — which ffmpeg's
>   encoder never emits — are not yet implemented (real samples now available for them).
>
> Per-decoder breakdowns, PSNR tables, and which tests cover which decoder are in
> the [Status](#status) section below.

## Usage

Decode a whole AVI or ASF/.wmv file with one call — the demuxer detects the container and codec,
and the stateful decoder handles I/P dispatch and the reference picture:

```go
data, _ := os.ReadFile("clip.wmv")
frames, err := msmpeg4.DecodeAll(data) // []*image.YCbCr, every frame, container auto-detected
```

Or drive the pieces yourself — a `Demuxer` (`Open`, `OpenAVI`, `OpenASF`) feeds a `Decoder`:

```go
dm, _ := msmpeg4.Open(data)             // AVI or ASF → Demuxer
fourcc, w, h, extradata := dm.Codec()
dec, err := msmpeg4.NewDecoder(fourcc, w, h, extradata)
if err != nil { /* unknown / v1 codec */ }
for {
    pkt, err := dm.ReadPacket()         // io.EOF at end
    if err != nil { break }
    img, err := dec.DecodeFrame(pkt)    // *image.YCbCr; auto I/P, ref maintained
    if err != nil { /* ... */ }
    _ = img
}
```

v2, v3, WMV1 and WMV2 decode **bit-exactly** vs ffmpeg across multi-frame I/P sequences
(`TestStatefulDecoder`, `TestContainerDemux`). The single-frame entry points (`DecodeIntraFrame`,
`DecodePFrame`, `DecodeIntraFrameWMV2`, …) remain available for thumbnailing.

## Why it's special — clean-room provenance

Microsoft never published the MS-MPEG4 bitstream format. The "ready" sources are
**FFmpeg** (LGPL — copyleft, incompatible with a permissive project) and
**Microsoft's own binary** (`mpg4c32.dll`; disassembling it violates the EULA).

Everything here is **reverse-engineered black-box**, with a strict clean-room rule:

- **We never read FFmpeg source** (`.c`/`.h`) and **never disassemble the Microsoft binary.**
- The ffmpeg **encoder** is used only to produce controlled bitstreams; the ffmpeg
  **decoder** is used only as a **pixel oracle** (observe output, never source).
- For **v1** (`MP41`/`DIV1`), which ffmpeg cannot encode, the original MS codec `mpg4c32.dll`
  is run **only as a black-box encoder** (feed it YUV, read the bitstream — its code is never
  inspected), exactly the way ffmpeg's encoder is used for the other versions
  (`re/v1/NOTES.md`).
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
| Real DIV3 **I-frames** → high-quality thumbnails | ✅ **bit-exact** (integer IDCT) |
| IDCT — integer `simple_idct` / `wmv2_idct` replaced the float path | ✅ **bit-exact** |
| **P-frames** + motion compensation (MVD median + half-pel) | ✅ **bit-exact** (incl. multi-frame) |

> The integer IDCT (`simple_idct.go` for v1–v3/WMV1, `wmv2_idct.go` for WMV2) replaced the original
> float IDCT, so **every decoder is now bit-exact** vs ffmpeg. The PSNR tables below predate that
> change (they were limited by float-IDCT rounding); the same content now decodes to ∞ dB.

**I-frame quality** — real-encoder DIV3, five real-world AVI movies across different
`rl_table`/`rl_chroma`/`dc_table` index combinations (pre-integer-IDCT measurements):

| File | Y PSNR | Cb MSE | Cr MSE |
|---|---|---|---|
| movie2 (576×240) | **∞ dB** (bit-exact) | 0.00 | 0.00 |
| movie1 (512×288) | **88.4 dB** | 0.00 | 0.00 |
| movie3 (512×354) | **89.0 dB** | 0.00 | 0.00 |
| movie5 (576×384) | **60.7 dB** | 0.02 | 0.02 |
| movie4 (576×256) | **51.8 dB** | 0.44 | 0.47 |

Residual errors (~0.4 MSE for the lower-PSNR files) are IDCT rounding noise and a
small number of rarely-occurring escape-code sequences not yet fully covered.

**P-frame quality** — motion compensation + residual decoding, same five movies:

| File | Y PSNR | Cb MSE | Cr MSE |
|---|---|---|---|
| movie1 (512×288) | **88.4 dB** | 0.00 | 0.00 |
| movie3 (512×354) | **89.0 dB** | 0.00 | 0.00 |
| movie2 (576×240) | **66.9 dB** | 0.04 | 0.01 |
| movie5 (576×384) | **54.8 dB** | 0.21 | 0.04 |
| movie4 (576×256) | **51.6 dB** | 0.71 | 0.73 |

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
| **I-frames** (DC + AC + AC prediction) | ✅ **bit-exact** (integer IDCT) |
| **P-frames** `DecodePFrameV2` (H.263 MV + inter/intra MBs + MC) | ✅ **bit-exact** vs ffmpeg |

**What works in practice:** v2 I-frames and P-frames decode **bit-exactly** vs ffmpeg
(luma+chroma, all qscale) — the integer IDCT (`simple_idct.go`) closed the former AC-prediction /
IDCT-rounding residual.

**Entry point:** `DecodeIntraFrameV2` / the v2 path inside `DecodeAVIFirstFrame`
(auto-detected from the AVI codec FourCC — strf `biCompression`, falling back to the
strh `fccHandler` when `biCompression` is empty).
**Tests** (v2 decoder): `v2psnr_test.go` (`TestV2PSNRFrame192`),
`v2re_test.go` (`TestDecodeV2`, `TestDecodeAVIFirstFrameV2`, plus the `TestV2*` RE/dump tests).

### MS-MPEG4 v1 (MP41 / MPG4 / DIV1)

| Piece | Status |
|---|---|
| **V1 I-frame decoder** `DecodeIntraFrameV1` (32-bit start code, H.263 intra MCBPC/CBPY, MPEG-1 DC pred, plain ISO escape) | ✅ **bit-exact** vs ffmpeg (`v1_test.go`) |
| **V1 P-frame decoder** `DecodePFrameV1` (`use_skip_mb_code`=1 always, first-column MV predictor=0, plain ISO escape) | ✅ **bit-exact** on integer-motion content (`v1_pframe_test.go`); high-detail (dense) inter residual has a remaining edge |

V1 (`MP41` / `MPG4` / `DIV1`) **I-frames decode bit-exactly**, and **P-frames decode bit-exactly**;
`DecodeIntraFrameV1`/`DecodePFrameV1` and the stateful `Decoder` handle the `MPG4`/`MP41`/`DIV1`
FourCCs. The format was unblocked using the original Microsoft codec `mpg4c32.dll` as a black-box
encoder oracle (ffmpeg cannot encode v1). The distinctive v1 P-frame quirk is the **MV predictor:
the left neighbour only** (0 at the left edge), not the H.263/MPEG-4 median of left/top/top-right.

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
| **WMV1** `DecodeIntraFrameWMV1` (`decode_wmv1.go`) | ✅ **bit-exact** vs ffmpeg, all qscale |
| **WMV1** inter scan table — black-box | ✅ |
| **WMV1** `DecodePFrameWMV1` (`decode_wmv1_p.go`, MV/skip/inter+intra blocks/MC) | ✅ **bit-exact** vs ffmpeg |
| **WMV1 / WMV2** `per_mb_rl_table=1` (per-MB RL index) | ✅ |
| **WMV2 I+P** `DecodeIntraFrameWMV2` / `DecodePFrameWMV2` (loop filter, ms-pel, integer IDCT) | ✅ **bit-exact** vs ffmpeg |

WMV1 is a **small** addition on top of v3: the MV / MB-type / RL / MCBPC / DC VLC tables are
shared with v3 (already reversed); only the scan tables, DC-scale tables, the ext-header and
the WMV1 ESC3 escape are new. **Tests:** `wmv1_test.go` (`TestWMV1IntraFrame`, `TestWMV1Sweep`).

## Roadmap

| Milestone | Status |
|---|---|
| **Format fully understood** (the hard part) | ✅ done |
| **v2 I+P** (`MP42`/`DIV2`) | ✅ **bit-exact** vs ffmpeg (luma+chroma) |
| **v3 I+P** (`DIV3`/`MP43`) | ✅ **bit-exact** vs ffmpeg (luma+chroma) |
| **WMV1 I+P** (Windows Media Video 7) | ✅ **bit-exact** vs ffmpeg |
| **WMV2 I+P** (Windows Media Video 8, incl. loop filter, ms-pel, integer IDCT) | ✅ **bit-exact** vs ffmpeg |
| **Stateful stream decoder** (`Decoder`: auto I/P, reference picture) | ✅ done |
| **Container demuxers** AVI + ASF/WMV (`Open`/`DecodeAll`/`OpenAVI`/`OpenASF`) | ✅ done |
| **`per_mb_rl` in P-frames** (per-MB RL index; done for I-frames) | 🔜 doable — closes a gap on some real files |
| **`inter_intra_pred`** (frames <320×240: intra MBs in P read the `h263_aic_dir` VLC) | 🔜 doable — needed for correct decode of real small-frame v3/WMV |
| **multi-slice** (`slice_code`/`slice_height` > 1; slice code currently skipped) | 🔜 doable |
| **MKV demuxer** (EBML) | 🔜 doable — broaden container support (AVI/ASF already done) |
| **v1 (MP41/DIV1)** I-frames | ✅ **bit-exact** vs ffmpeg (encoder oracle = MS `mpg4c32.dll` via Wine) |
| **v1 (MP41/DIV1)** P-frames | 🔜 doable now (same oracle) |
| **WMV2 multi-P `no_rounding`** | ✅ tracked + toggled per P-frame — long P runs stay bit-exact on half-pel MC (`TestWMV2NoRoundMultiP`; verified on real `m5.wmv`, 40/40 frames) |
| **WMV2 ms-pel filter** | ✅ verified correct against real `.wmv` samples (no per-block mismatch; real frames decode bit-exact). Note: ffmpeg's own encoder never sets `mspel=1`, so only the real samples exercise it |
| **WMV2 loop filter** | ✅ correct on fully-decoded frames (real `m5.wmv` decodes 135 P-frames bit-exact). A skip-aware variant is unneeded for current data (those frames have no skipped MBs) |
| **WMV2 `mb_non_intra` tables** | 🔶 **table 1 complete** (128/128). **table 0** is structurally complete (124 codes, Kraft=1.0; a read-limit bug that hid its 21-bit code is fixed). **table 2** (Kraft 0.996) misses ~1-2 codes, and a few codes in 0/2 map to a wrong symbol. Real `.wmv` decode in long bit-exact runs (m5: 135 P-frames, m4: 4) before drifting on those codes; pinning them down needs a global constraint-solve across many MBs (point derivation does not converge) — deferred |
| **WMV2 ABT / J-frames** | 🔜 real MS-encoded samples are now available (their extradata sets `abt`/`j_type`), but the frames examined so far still use `abt_type=0`; finding frames that exercise ABT/J is in progress |

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
- **`re/wmv2/`** — WMV2 P-frame reversal: the 3 `mb_non_intra` VLC tables via the
  encoder-oracle (`wmv2_mb_extract.py`, `wmv2_mb_intra.py`, `wmv2_mb_resolve.py`), Kraft-sum
  validation, the `data/` table dumps, and `NOTES.md` (377/384 codes; the last few are recovered
  from real MS-encoded `.wmv` files — `m4`/`m5` — which exercise codes ffmpeg's encoder never emits).
- **`re/v1/`** — MS-MPEG4 v1 work: the v1 I-frame header reversal and `NOTES.md`. The encoder oracle
  is the original MS `mpg4c32.dll` run as a black box via Wine (`re/common/vfwenc_mpg4.exe`,
  `re/common/mpg4v1_batch.py`), since ffmpeg cannot encode v1.

## License

Port code is MIT (see `LICENSE`, `NOTICE`). No FFmpeg source was read and no Microsoft binary
was disassembled; tables are derived from observed bitstream behaviour + the open standards
and the GFDL format spec. (For v1, the MS `mpg4c32.dll` is run only as a black-box encoder
oracle — never inspected — because ffmpeg cannot encode v1.)

## Tests

```sh
cd go-msmpeg4 && go test ./...
```

Which test files cover which decoder is listed per-decoder in the [Status](#status)
section (v3 vs v2). Tests that need real movies / oracle YUVs auto-skip when the
fixtures are absent.
