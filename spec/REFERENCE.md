# Spec reference — ITU-T H.263 (1996) + MPEG-4 Part 2 notes

Local copies:
- `h263v1.pdf` — full ITU-T H.263 draft (1996), 58 pages.
- `h263.txt` — extracted text (search this).
- `mpeg4p2_intro.pdf` — ISO 14496-2 introduction only (no tables).

H.263 is the **base** MS-MPEG4 v3 (DIV3) is a variant of. The *structure* (MB layer,
MCBPC/CBPY/DQUANT, TCOEF EVENT format, MVD VLC, motion comp, dequant, IDCT, zigzag)
comes from here. MS-MPEG4 v3 **deviates**: VLC-coded intra DC (MPEG-4 style, not the
fixed 8-bit INTRADC of H.263), its own TCOEF VLC table, AC prediction + alternate scan
(MPEG-4 Part 2), and a joint MCBPCY table. Those MS deviations are reverse-engineered
in `../re/` (black-box). This file records the **standard** pieces that transfer.

## Confirmed identical in MS-MPEG4 v3 (verified by our black-box reversal)

- **Dequant** (§6.2.1): `|REC| = QUANT·(2·|LEVEL|+1)`, then `−1 if QUANT even`
  (oddification — prevents IDCT mismatch). `REC = sign(LEVEL)·|REC|`. ✅ matches ours.
- **Zigzag** (§6.2.3 FIGURE 13) — standard zigzag. ✅ matches ours.
- **IDCT** (§6.2.4) — separable 8×8, C(0)=1/√2. ✅ matches ours.
- INTRADC reconstruction uses a DC scaler (MS uses MPEG-4 dc_scaler, not H.263's ÷8).

## MCBPC for I-pictures — TABLE 4/H.263 (structure; MS codes differ)

| Index | MB type | CBPC(56) | bits | Code |
|--|--|--|--|--|
| 0 | 3 INTRA   | 00 | 1 | `1` |
| 1 | 3 INTRA   | 01 | 3 | `001` |
| 2 | 3 INTRA   | 10 | 3 | `010` |
| 3 | 3 INTRA   | 11 | 3 | `011` |
| 4 | 4 INTRA+Q | 00 | 4 | `0001` |
| 5 | 4 INTRA+Q | 01 | 6 | `000001` |
| 6 | 4 INTRA+Q | 10 | 6 | `000010` |
| 7 | 4 INTRA+Q | 11 | 6 | `000011` |
| 8 | Stuffing  | -- | 9 | `000000001` |

**MB type 4 = INTRA+Q** → a 2-bit **DQUANT** follows (TABLE 9: `00`→−1, `01`→−2,
`10`→+1, `11`→+2; QUANT clipped to 1..31). This is the missing half of MS's joint
MCBPCY that real DIV3 frames use and our crafted (constant-QP) frames never produced.

## TCOEF EVENT format (§5.4.2) — MS uses its own VLC, same *structure*

- Each EVENT = (LAST, RUN, LEVEL) + sign bit (`s`: 0=positive).
- **ESCAPE** (H.263) = `0000 011` then FLC: LAST(1) + RUN(6) + LEVEL(8, TABLE 14).
  (MS deviates — our ESC1 level-offset + ESC3 13-bit field, reversed in `re/`.)

## Motion vectors / P-frames (§6.1) — transfers to MS P-frames

- **MVD VLC** = TABLE 11/H.263 (full table in `h263.txt`, half-pel resolution,
  range −16..+15.5, the index↔value wrap-around handled by the table).
- **MV prediction = MEDIAN** of 3 candidates: MV1=left, MV2=top, MV3=top-right
  (FIGURE 11). Border rules: INTRA/not-coded neighbour → 0; left-edge MV1→0;
  top-edge MV2,MV3→MV1; right-edge MV3→0. Median per component.
- **Half-pel MC** (§6.1.2) bilinear: `a=A`, `b=(A+B+1)/2`, `c=(A+C+1)/2`,
  `d=(A+B+C+D+2)/4` (`/` = truncating division).
- Coefficients clipped to −2048..2047; IDCT output clipped −256..255.

## Still MS-specific → reverse-engineer via black-box (`re/`)

- Intra DC VLC (done: 129 luma + 205 chroma) + DC gradient prediction (done).
- Intra TCOEF VLC (done: last=1 + escape; last=0 partial — 32 verified).
- **AC prediction + alternate scan** (MPEG-4 Part 2 §7.4.x) — derive via black-box.
- **Joint MCBPCY INTRA+Q** half — derive (needs QP-varying frames).
- Picture-header layout differences (the 10-bit field in ffmpeg-crafted frames).

## Annex I (Advanced INTRA Coding) — EXACT scans + AC prediction [from h263_2005.pdf]

### Alternate scans (Figure I.2) — raster (row,col) -> scan position (1-indexed)
Alternate-HORIZONTAL (used for Vertical prediction / INTRA_MODE=1):
   row0:  1  2  3  4 11 12 13 14
   row1:  5  6  9 10 18 17 16 15
   row2:  7  8 20 19 27 28 29 30
   row3: 21 22 25 26 31 32 33 34
   row4: 23 24 35 36 43 44 45 46
   row5: 37 38 41 42 47 48 49 50
   row6: 39 40 51 52 57 58 59 60
   row7: 53 54 55 56 61 62 63 64
Alternate-VERTICAL (as in H.262/MPEG-2; used for Horizontal prediction / INTRA_MODE=2):
   row0:  1  5  7 21 23 37 39 53
   row1:  2  6  8 22 24 38 40 54
   row2:  3  9 20 25 35 41 51 55
   row3:  4 10 19 26 36 42 52 56
   row4: 11 18 27 31 43 47 57 61
   row5: 12 17 28 32 44 48 58 62
   row6: 13 16 29 33 45 49 59 63
   row7: 14 15 30 34 46 50 60 64

### AC prediction
- INTRA_MODE per MB (H.263 Table I.1: VLC 0=DC-only, 10=Vertical, 11=Horizontal).
  MPEG-4/MS variant uses a 1-bit ac_pred_flag with direction IMPLICIT from DC gradient
  (|grad_left| vs |grad_top|, same selector as DC prediction).
- Mode Vertical (1): predict first ROW from block ABOVE; scan = Alternate-Horizontal.
- Mode Horizontal (2): predict first COLUMN from block LEFT; scan = Alternate-Vertical.
- Mode DC-only (0): only DC predicted (avg above+left); scan = zigzag.
- RecC(u,v) = 2*QUANT*LEVEL (Annex I, no dead-zone). Final = RecC + predictor, DC
  oddification, clip. (MS likely keeps MPEG-4 q*(2L+1) for AC — verify; our ap=0 reversal
  matched q*(2L+1).)
- NB: MS keeps a SEPARATE intra DC VLC (we reversed 129/205) + DC prediction — i.e. MS
  follows MPEG-4 Part 2, NOT H.263 Annex I's "DC-as-AC" treatment.

Full INTRA TCOEF (Table I.2, 102 entries) is in h263_2005.txt ~line 3560 — codewords
identical to normal TCOEF (Table 16) but remapped (RUN,LEVEL). MS uses its OWN intra
TCOEF (our reversal: (0,1,last1)=`11`, not Annex I `0111`) — so MS = MPEG-4 table, reverse stays.

## DC/AC prediction algorithm [MPEG-4 Part 2 CD §7.3.3, spec/mpeg4_part2.pdf]

### Direction (§7.3.3.1) — A=left, B=above-left, C=above
  if |FA[0][0] - FB[0][0]| < |FB[0][0] - FC[0][0]|:  predict from C (above)
  else:                                              predict from A (left)
  Unavailable neighbour (outside VOP / not intra): F[0][0] = 2^(bits_per_pixel+2) = 1024.
  => BLOCK0 of first MB: A,B,C all unavailable => 0<0 false => predict from A (LEFT)
     => scan = ALTERNATE-VERTICAL ; DC pred adds FA[0][0]//dc_scaler = 1024//dc_scaler.

### DC prediction (§7.3.3.2)
  predict from C: QFX[0][0] = decoded + FC[0][0] // dc_scaler
  predict from A: QFX[0][0] = decoded + FA[0][0] // dc_scaler   (dc_scaler from Table 7-2)

### AC prediction (§7.3.3.3) — only if ac_pred_flag=1
  predict from A (left):  first ROW   QFX[0][i] = decoded[0][i] + (QFA[0][i] * QPA) // QPX
  predict from C (above): first COL   QFX[j][0] = decoded[j][0] + (QFC[j][0] * QPC) // QPX
  If predictor block (A or C) is outside VOP/packet: ALL its prediction coefs = 0.
  => BLOCK0: predict from A, A unavailable => AC predictor = 0 => coded = actual.
  Scan select: predict-from-A(horizontal) -> alternate-VERTICAL; predict-from-C(vertical)
  -> alternate-HORIZONTAL; ac_pred_flag=0 -> zigzag.

KEY: our earlier "(0,1) L2->L1" confusion was a SCAN error — block0 uses alternate-VERTICAL
(predict-from-A), not alt-horizontal. With alt-vertical + predictor 0, coded == actual.
MS intra TCOEF is still its own table (Table 11-15 here is the MPEG-4 reference, differs).
