# MS-MPEG4 v1 (MPG4 / MP41 / DIV1) — reverse-engineering notes

Encoder-oracle: MS codec `mpg4c32.dll` via Wine + `re/common/vfwenc_mpg4.exe` (FourCC MPG4),
driven by `re/common/mpg4v1_batch.py`. Pixel-oracle: ffmpeg `msmpeg4v1` decodes the output.
Black-box only (no disassembly). Open refs: H.263 spec (spec/h263*.pdf), msmpeg4.txt (GFDL).

## I-frame header (confirmed empirically by varying the encode + parsing)
32-bit startcode `00 00 01 00` + Frame#(5) + PictureType u(2)=00(I) + Quant u(5) + slice_code u(5).
(NB: spec calls the startcode "u(24) 0x100"; the codec emits a 32-bit `00 00 01 00`, so skip 32 bits.)
ext_header is at the END of the I-frame (v1-3), not the start.
The VFW quality param does NOT change qscale — the codec always emits q=4. Enough for RE/verify.

## MB coding (v1 vs v2) — to verify
- luma_scale = chroma_scale = 8 (constant, like v2/MPEG-1).             [spec]
- intra MCBPC = H.263 intra MCBPC VLC (NOT the v2 table).               [to confirm]
- ac_pred = 0 (no ac_pred, no ac_pred bit).                            [to confirm]
- DC prediction = MPEG-1 style (prev block of same component, slice-reset), NOT v2 gradient. [spec L147]
- CBPY = H.263 CBPY VLC.                                                [to confirm]
- AC RL tables: TBD (v2 used lumaTCOEF[2]/chromaTCOEF[2]).

## go decoder v1 (decode_v1.go / decode_v1_p.go) — STATUS: I + P bit-exact ✅
DecodeIntraFrameV1: header (startcode32 + frame5 + pictype2 + quant5 + slice5) + H.263 intra MCBPC +
H.263 CBPY + per-block DC (decodeV2DC) + MPEG-1 DC prediction (per-component, reset per row) + AC
(plain ISO escape `decodeTCOEFv1`, zigzag, lumaTCOEF[2]/chromaTCOEF[2]) + dcScale=8 + simpleResidual.
**I-frames bit-exact** vs ffmpeg (`v1_test.go`).

DecodePFrameV1: `use_skip_mb_code`=1 always (one skip bit per MB), fixed selectors rl=2 / dc-scale=8 /
mv=0, decodeV2MBType + decodeV2CBPYInter (inter CBPY complement `if (cbp&3)!=3 { cbp ^= 0x3C }`) +
decodeV2MV, MC via mcFill, inter blocks via the plain ISO escape (decodeInterBlockV1), intra-in-P with
H.263 CBPY + MPEG-1 DC prediction. **P-frames bit-exact** vs ffmpeg (`v1_pframe_test.go`).

**Key v1 quirk (the only non-obvious thing): the MV predictor is the LEFT NEIGHBOUR ONLY** (0 at the
left edge), NOT the H.263/MPEG-4 median of left/top/top-right. Found with a per-coefficient pixel
oracle: for a cbp=0 (pure-MC) MB on unambiguous content, `ff_pixels = MC(true_mv)`, so the true MV is
recovered by brute-forcing mcFill; it equalled the left neighbour while the median mis-predicted. The
earlier "first column → predictor 0" finding is just the special case (left neighbour = 0 at mx=0).
decodeInterBlockV1 / escape / dequant / MC were all already correct; the predictor was the lone bug.

Earlier intra dead-ends (kept for the record): the AC RL table was once thought incomplete — it turned
out v1 uses the plain ISO escape (`decodeTCOEFv1`), not the v3 variable escape, which fixed grad/rand
intra. v1 does NOT predict the luma CBP (adding H.263 CBP prediction broke the smooth clip).
