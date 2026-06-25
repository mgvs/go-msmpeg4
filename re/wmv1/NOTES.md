# WMV1 (Windows Media Video 7) — black-box notes

WMV1 reuses the v3 VLC tables (MV / MB-type / RL / MCBPC / DC) and differs only in:
scan tables, DC-scale tables, the picture header, and the AC escape coding (`run_diff`,
ESC3). ffmpeg has a WMV1 encoder+decoder, so the same encoder/decoder-oracle method applies.

## Picture header (I-frame, q=4, 16×16) — discovered by probing
```
pictype(2)=00 | qscale(5) | slice_code(5)  [0x17 = one slice]
ext-header (WMV "decode_ext_header": frame-rate / bit-rate / flags) — constant for fixed
            encoder settings; pushes the MB layer to bit 35 in our 16×16 q4 frames
[per_mb_rl_table(1) only if bit_rate>MBAC] | rl_chroma c3 | rl_table c3 | dc_table(1)
```
For our probes: MCBPC starts at **bit 35**, **dc_table_index = 1**, **rl_table_index = 2**.
The 44-bit constant prefix (header + MCBPC[block0-only `010101`] + ac_pred=0 + block0 DC=0)
is `00001001011111001000110000111010111010101010`.

## AC escape (differs from v3)
- Intra `run_diff = 1` (v3 = 0); only applied inside the *second* escape.
- THIRD escape (ESC3) is variable-length (NOT v3's last+run6+level8):
  `esc + "00" + last(1)` then, on the first ESC3 of the frame, the level/run bit-lengths:
  `q<8: level_len = u(3) (0 -> 8+u(1))`, else unary; `run_len = u(2)+3`. Then
  `run(run_len) | sign(1) | level(level_len)`, and `i += run+1; if(last) i+=192` with the
  `if(i>62) i-=192` last-flag trick.

## Scan tables — method
Decoder-oracle: reuse the constant prefix, append a hand-built **ESC3** that explicitly
encodes `(run=R, level=1, last=1)` (run_len forced to 6 bits → R in 0..62). WMV1 places that
single coefficient at scan index R+1; patch into a real WMV1 AVI, decode with ffmpeg, DCT
block-0 → the coefficient's (u,v) gives `scan[R+1] = (u,v)`. (`re/wmv1/wmv1_scan_oracle.py`.)

### WMV1 intra zig-zag (derived, 64/64, valid permutation) — saved /tmp/wmv1/intra_scan.json
```
{0,0},{1,0},{0,1},{0,2},{1,1},{2,0},{3,0},{2,1},{1,2},{0,3},{0,4},{1,3},{2,2},{3,1},{4,0},{5,0},
{4,1},{6,0},{3,2},{2,3},{1,4},{0,5},{0,6},{1,5},{2,4},{3,3},{4,2},{5,1},{7,0},{6,1},{7,1},{5,2},
{4,3},{3,4},{2,5},{1,6},{0,7},{1,7},{2,6},{3,5},{4,4},{5,3},{6,2},{7,2},{6,3},{7,3},{5,4},{4,5},
{3,6},{2,7},{3,7},{4,6},{5,5},{6,4},{7,4},{6,5},{7,5},{5,6},{4,7},{5,7},{6,6},{7,6},{6,7},{7,7}
```
(differs from the v3 zigzag, as the spec says.)

## TODO (remaining for a WMV1 decoder)
- alt-horizontal / alt-vertical scans (ac_pred=1 → needs a 2-MB probe so AC prediction is
  active; same ESC3 oracle, read the predicted block).
- inter scan (P-frame oracle).
- DC-scale tables (luma/chroma) via DC sweeps (or confirm a formula).
- Go decoder: WMV1 header parse + ESC3 + run_diff + the 4 scans + DC-scale; verify vs ffmpeg.

## Derived (2026-06-25) — all WMV1 I-frame tables

### intra alternate scans (decoder-oracle, ESC3)
- **alt-vertical** (block0, ac_pred=1, dir=0; no neighbours -> no AC pred added): 64/64 perm.
  `{0,0},{1,0},{2,0},{0,1},{3,0},{4,0},{5,0},{1,1},{0,2},{0,3},...` (column-biased)
- **alt-horizontal** (block2 bottom-left, ac_pred=1, dir=1 forced by a nonzero block0 DC;
  block0 uncoded -> predicted AC row = 0): 64/64 perm.
  `{0,0},{0,1},{1,0},{0,2},{0,3},{1,1},{2,0},{3,0},...` (row-biased)
  (NOT the transpose of alt-vertical — derived independently.)
Script: `re/wmv1/wmv1_scans.py`. Saved /tmp/wmv1/scan_{intra,altv,alth}.json.

### DC-scale tables (encoder-oracle gray sweep; dcScaler = 8*span/(n_unique-1))
`re/wmv1/wmv1_dcscale.py`. q=1..31:
```
luma  : 8,8,8,8,8,9,9,10,10,11,11,12,12,13,13,14,14,15,15,16,16,17,17,18,18,19,19,20,20,21,21
chroma: 8,8,8,8,9,9,10,10,11,11,12,12,13,13,14,14,15,15,16,16,17,17,18,18,19,19,20,20,21,21,22
```
(differs from the v3 / ff_mpeg4 scaler -> WMV1 needs its own.) Saved /tmp/wmv1/dcscale.json.

## Status: WMV1 **I-frame** table set COMPLETE
header layout + ESC3 + 3 intra scans + DC-scale (luma/chroma) all derived black-box. Enough
for an I-frame decoder (thumbnails). Remaining for P-frames: **inter scan** (P-frame oracle)
+ the WMV1 P-frame header / MV / inter-block path. Then a Go WMV1 decoder + PSNR verify vs ffmpeg.
