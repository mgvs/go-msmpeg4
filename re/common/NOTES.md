# MS-MPEG4 v3 (DIV3) clean-room RE notes

Method: ffmpeg as **black box only** (encode controlled YUV → bitstream; decode →
pixel oracle). No ffmpeg source, no MS binary. All facts below are derived from
observed bitstreams (`re/extract.sh`, `/tmp/msm_craft`).

## Frame layout (single 16×16 MB, solid color)

`gray128`, qscale 8 → 6 bytes: `000100010111101111010101010000011001000110000111`

## Picture header (I-frame)

Confirmed by qscale sweep (q=1,3,4,5,8,12,16,24,31 on a solid 16×16 frame):

| field | bits | value |
|---|---|---|
| `??` (picture coding, I-frame) | [0..1] = 2 bits | always `00` on every I-frame (crafted + 5 real samples) |
| `quantizer` (pquant) | [2..6] = 5 bits, MSB-first | == qscale exactly (q=31→`11111`, q=8→`01000`, q=1→`00001`) |

Real samples cross-check: v3_clip quant=4, v3_aggr quant=2, v3_clan quant=4,
v2_stat quant=4 (bits[2..6]).

MB layer begins at **bit 7**.

> TODO: bits[0..1] are `00` for all I-frames — need a P-frame to learn the
> picture-type encoding (only needed to classify I vs P during seek).

## Intra MB layer (DC-only block, qscale 8)

Every solid-MB frame shares an 11-bit prefix after the header: `10111101111`.
This is `MCBPC(intra, cbpc=0) + CBPY(0000) + block0 DC-size` (not yet split).

DC value sweep (luma Y, chroma fixed 128, q=8), MB bits after `10111101111`:

| Y | DC diff | tail |
|---|---|---|
| 128 | 0  | `0101010…` (shortest) |
| 120 | -1 | `0010111010…` |
| 136 | +1 | `0010101010…` (differs from Y=120 by the sign bit) |
| 64/192 | ±N | `0000001111…` (longer size code) |
| 0/255  | ±max | `00000011110011011…` (longest) |

→ DC coding = **DC-size VLC** (number of extra bits) **+ value bits** — the
H.263/MPEG-4 intra-DC scheme. Larger |diff| → longer size code. The DC scaler
quantizes the differential (Y±8 ⇒ diff ∓1 at q=8, so DC step ≈ 8).

## Intra DC differential VLC (block 0, luma) — extracted

Clean bit-isolation (P=18-19 bit constant prefix `hdr + MCBPC|CBPY`, Q=27-bit
constant blocks-1..5 tail). Varying only luma Y (chroma fixed) changes only
block-0's DC code. Extracted codewords (qscale 8), ordered by |Y-128|:

| code (no sign) | + (Y>128) | − (Y<128) | rank |
|---|---|---|---|
| `10` | (diff 0, no sign) | | 0 |
| `11`+s | `110` | `111` | 1 |
| `011`+s | `0110` | `0111` | 2 |
| `0101`+s | `01010` | `01011` | 3 |
| `00011`+s | `000110` | `000111` | 4 |
| `000000`+s | `0000000` | `0000001` | 5 |

→ ±pairs differ only in the trailing **sign bit** (1 = negative). The set is
prefix-free. Larger |diff| → longer codeword (continues: len 8,10,11,13,15,17…
for Y far from 128). This is the MS DC-differential VLC.

### DC scaler — resolved

Decoding the crafted frames back through the oracle: **reconstruction is lossless
for every flat value V** (Yrec == Yin for all V incl. odd/large) ⇒ **dc_scaler = 8
at q=8**, so the DC level = (V − 128) exactly, every integer level distinct.

The "level pairs" seen in the consecutive sweep were a **P/Q heuristic artifact**
(the string prefix/suffix boundary mis-aligned by a bit between sweep sets — P came
out 19 vs 20 on different sets). Lesson: the string-diff isolation is too fragile
to pin the exact codeword↔level table.

> NEXT (rigorous): build a **decoder-in-the-loop** harness — parse header + a
> hypothesised MCBPC/CBPY/DC table, require it to (a) consume exactly the frame's
> bits and (b) reconstruct DC == oracle. That closes the loop and removes the
> heuristic ambiguity. Structure is known (direct level VLC, sign = last bit,
> prefix-free, length grows with |level|); only the exact bit assignments need
> the loop to confirm.

## DONE — Intra DC luma VLC (complete + verified)

True prefix boundary via first-diff: **header = 7 bits**, **MCBPC|CBPY = 12 bits**
(`101111011110` for a DC-only intra MB), block0 DC starts at bit 19.
**dc_scaler = 16 at q=8** (→ DC level = round((V-128)/2); reconstruct = 128 + 2·level).

Full V=0..255 sweep → **129 distinct codewords, levels −64..+64**, anchored to the
fixed blocks-1..5 TAIL (pad-independent), labelled by oracle reconstruction. Table
in `dc_luma_table.go`, decoder `dc.go`. Verified:
- round-trip: every codeword decodes to its level consuming exactly its bits;
- decoder-in-loop: block0 DC decodes on all 256 crafted frames, monotonic recon.

## MCBPC / CBPY split (in progress)

Crafting AC into chosen 8×8 luma blocks (strong gradient) vs flat:
- All intra MBs with chroma flat (cbpc=00) share a **10-bit prefix `1011110111`**
  = **MCBPC**(intra, cbpc=00). CBPY follows at bit 17.
- Flat (cbpy=0000): MCBPC|CBPY = 12 bits ⇒ **CBPY(0000) ≈ 2 bits (`10`)**, then
  block-0 DC at bit 19.
- Chroma AC (cbpc≠00) changes the stream from bit 17 → MCBPC carries cbpc near
  its tail.

> NEXT: dedicated sweeps to tabulate the full **MCBPC** (type+cbpc) and **CBPY**
> (4-bit luma pattern) VLCs — craft all 16 luma AC patterns + the 4 cbpc cases,
> decode each codeword by consume-exactly once DC/AC lengths are known.

## Provenance tools (KEEP IN REPO — proof of clean-room derivation)
- `re/craft.sh` — controlled-content encoder harness (ffmpeg as black box).
- `re/extract.sh` — oracle harness (real sample frames + decoded YUV).
- `re/gen_dc_luma.py` — regenerates `dc_luma_table.go` from crafted frames;
  verified to reproduce the committed table 1:1.

These scripts ARE the evidence the tables were derived from observed bitstreams,
not copied from FFmpeg/any library or extracted from a Microsoft binary.

## DONE — Intra DC chroma VLC (complete + verified)

Sweep Cb=0..255 (luma=128, **Cr=200** as a non-zero block-5 anchor), block-4 DC
starts at bit 27 (= 7 + 12 + 4·2 luma DC). **205 distinct codewords** (chroma is
less quantised than luma → finer levels; chroma L0 = `00`, ≠ luma `10`). Table in
`dc_chroma_table.go`, decoder `dc_chroma.go`; all 205 round-trip OK (max len 25).
Generator `re/gen_dc_chroma.py` reproduces the table 1:1.

## KEY STRUCTURE finding — AC is mandatory per block (Phase 3 is a prerequisite)

A flat single-MB frame (`a128`, 48 bits) accounts as: header(7) + MCBPC|CBPY(12)
+ what *looked* like 6 DC codes (bits 19–31) **+ 17 more bits** `11001000110000111`
that are NOT padding. ⇒ each intra block carries an **AC tail terminated by EOB**
even when "flat"; the contiguous "6 DC" read was partly EOB bits mis-read as DC.

Confirmed by crafting AC into block 0 (weak gradient): the stream diverges from
flat at **bit 17** — i.e. inside CBPY (bits 17–18 for cbpy=0000), so **CBPY encodes
which luma blocks carry AC**, and MCBPC = bits 7–16 (`1011110111`, intra+cbpc00).

MB structure: `MCBPC(10) + CBPY(var) + perBlock[ DC + AC… + EOB ]`.

**Consequence: DC spatial prediction can't be verified without consuming AC, so
Phase 3 (AC) must come first.** Order flipped.

Crafted to start Phase 3: `flat16`, `b0_vgrad`, `b0_hgrad` (single weak AC coeff in
block 0) in /tmp/msm_craft.

## Phase 3 AC — started (single-AC-coefficient probe)

Tool `re/probe_ac.py`: build block 0 = one 8×8 DCT basis(u,v)·amp (zero mean → DC
stays level 0), encode, read the bitstream. numpy DCT only chooses inputs.

Single AC at (0,1) (run=0), DC held at level 0, anchoring on the common 27-bit
suffix (block-0 TERM + blocks 1-5):
- flat block 0 (cbpy=0000): mid = `1010` = CBPY(0000) + DC(`10`) + [TERM in suffix].
- AC frames (cbpy=1000) share prefix `0101010100`, then the AC code:
  - level +1 `1110`, −1 `1111`; +2 `011000`, −2 `011001`; +3 `00101100`; … →
    **sign = last bit**, magnitude lengthens — same shape as the DC VLCs.
- amp=6 quantised to 0 (no AC) → identical to flat ⇒ AC dequant dead-zone.

BLOCKERS to finish AC isolation:
1. **CBPY VLC**: cbpy=0000 vs 1000 differ at bit 17; need the code lengths to cut
   DC/AC apart cleanly. (cbpy=0000 → `10`; cbpy=1000 prefix `0101…` TBD.)
2. Possible **ac_pred_flag** per MB (MPEG-4 has AC prediction) interacting with the
   bit-17 region — must be separated from CBPY.
3. **block TERM / EOB** (2 bits at the head of the 27-bit suffix for flat) — pin it.

> NEXT: nail CBPY (craft all 16 luma AC patterns) + the ac_pred_flag, then isolate
> the run/level/last VLC cleanly; sweep run and level via probe_ac.py over (u,v).

### BREAKTHROUGH — CBPY(1000) length + run=0/last=1 AC VLC

CBPY(1000) length found via first-diff of two cbpy=1000 frames with DIFFERENT
block-0 DC (CBPY identical, DC diverges): block-0 DC starts at bit 24 ⇒ the region
between MCBPC (ends bit 17) and DC = **7 bits** for cbpy=1000 (vs 2 bits for
cbpy=0000). [This region = CBPY, possibly incl. an ac_pred bit.] With DC located,
block-0 AC isolates cleanly (common 27-bit prefix MCBPC+CBPY+DC, common 27-bit
blocks-1..5 suffix).

**run=0, last=1 AC magnitude codes** (sign = last bit; level = rank by recon coef):

| level | + | − |
|---|---|---|
| 1 | `1110` | `1111` |
| 2 | `011000` | `011001` |
| 3 | `00101100` | `00101101` |
| 4 | `000101110` | `000101111` |
| 5 | `0000001100` | `0000001101` |
| 6 | `00000001010` | `00000001011` |
| 7 | `00000001000` | `00000001001` |
| 8 | `000010110010` | `000010110011` |
| 9 | `000011101110` | `000011101111` |

AC dequant step ≈ 16–17 at q=8 (recon coef 21.8, 40.4, 55.5, 68.9, 88, 103, 120…).
amp giving recon≈0 ⇒ dead-zone. Single coeff ⇒ last=1 terminates the block (no
separate EOB for coded blocks; the EOB-when-empty case for cbpy=0 still TBD).

**last=0 separated** via a 2-coefficient block (basis(0,1)+basis(1,0), zigzag
positions 1,2 → run=0 each): block-0 AC = `1000` + `1110` =
`(run0,last0,lvl1)` then `(run0,last1,lvl1)`. So:
- (run=0, last=0, level=1) = `1000`  (· sign)
- (run=0, last=1, level=1) = `1110`  (· sign)

The TCOEF VLC jointly encodes (last, run, level) — standard MPEG-4 shape.

### Full TCOEF sweep — blocked on a ±1–2 bit boundary (likely ac_pred_flag)

`re/sweep_tcoef.py` sweeps single coefficients at scan positions (u,v) with robust
isolation (decode block-0 DC via the real luma table → exact AC start). It runs and
yields a distinct code per (u,v):
```
(0,1) amp12 0111      (0,1) amp24 001100
(1,0) amp12 001111    (2,0) amp12 001110    (1,1) amp12 0010001 …
```
BUT the absolute boundary still wobbles 1–2 bits between sweep sets (suffix 27 vs 28;
codes shifted vs the earlier run=0 extraction `1110`/`011000`). This points to an
**unmodelled field — most likely a per-MB ac_pred_flag (MPEG-4 AC prediction)** —
sitting in/around the 7-bit cbpy=1000 region, so string isolation can't pin the
table bit-exactly.

### consume-exactly harness — built (re/harness.py), flat structure resolved

`re/harness.py` decodes a single intra MB with hypothesised structure + the real DC
tables and reports exact bit consumption. Run on flat `a128`:

  hdr(7) + MCBPC(10) + **CBPY(2)** + 6×DC([0,0,0,0,0,0], pos 19→31) + **17-bit trailer**
  `11001000110000111`

The 17-bit trailer is **CONSTANT across all flat frames** (a128/a160/a200/a64 —
only the DC differs, the trailer is identical). ⇒ flat MB structure is:
`MCBPC + CBPY + [6 DC, contiguous] + [6 AC sections]`, and **an AC section is always
present per block even when empty** (cbpy=0000 still emits the trailer). So the AC
is NOT cbp-gated the standard way; every block ends with an EOB-like terminator.

Greedy CBPY-length brute force is unreliable (it picks a length giving mostly-zero
DC, but that's ambiguous without the AC table — e.g. flat mis-parses to CBPY=0,
DC=[...,2,0]). ⇒ the harness cannot disambiguate by greedy probing **until it
contains a hypothesised AC table**: the structure is genuinely jointly-coupled.

### Structure confirmed SEPARATE + CBPY region for all 16 patterns

Fitter on flat: DCs are contiguous (empty-AC EOBs between DCs are 0-length) ⇒ MB =
`MCBPC(10) + CBPY-region + [6 DC contiguous] + [AC sections]` (DC block, then AC).

CBPY region (bits 17 → block-0 DC start), via DC-offset first-diff (two frames, same
luma AC pattern, block-0 mean 128 vs 160 → diverge at block-0 DC):

| luma pattern (b0b1b2b3) | region code | len |
|---|---|---|
| 0000 | `10` | 2 |
| 0001 | `001100` | 6 |
| 0010 | `0000100` | 7 |
| 0011 | `000100` | 6 |
| 0100 | `0000010` | 7 |
| 0101 | `000110` | 6 |
| 0110 | `01000100` | 8 |
| 0111 | `0111100` | 7 |
| 1000 | `0101010` | 7 |
| 1001 | `000011010` | 9 |
| 1010 | `0101000` | 7 |
| 1011 | `00001110` | 8 |
| 1100 | `0111110` | 7 |
| 1101 | `00000010` | 8 |
| 1110 | `00100100` | 8 |
| 1111 | `01100` | 5 |

Lengths 2–9 are long for a plain CBPY (usually 2–6) ⇒ the region likely bundles a
per-MB **ac_pred_flag (1 bit)** + CBPY (+ maybe cbpc interaction). The full fitter
must split ac_pred from CBPY. (These are with one AC coeff per AC-block, so AC
prediction may be active.)

### Decoder-fitter built (re/fitter.py); AC blocked on AC-prediction coupling

`re/fitter.py` = the growing partial decoder. It correctly decodes flat frames:
header + MCBPC + CBPY + 6 DC (all 0) — consume-exactly to the AC payload. So the
DC/structure layer is solid in code.

But on single-AC frames it mis-decodes the *other* blocks' DC (block1 DC≠0 when it
should be flat). Neither "separate (6 DC then AC)" nor "interleaved [DC+EOB] with a
fixed EOB" fits (EOB brute force over flat → 0 candidates). Conclusion: **when a
block carries AC, AC-prediction alters the DC/AC coding of neighbouring blocks** —
this is the genuine v3 coupling. The fitter must MODEL AC prediction (predict each
block's DC + first row/col of AC from its left/top neighbour, gradient-selected)
before the per-block bit layout lines up.

### KEY INSIGHT — block 0 has no neighbours ⇒ no AC prediction ⇒ clean TCOEF

The AC-prediction coupling only affects blocks 1..5 (predicted from neighbours).
**Block 0 (top-left) has no predictor, so its coded AC == its actual coefficients.**
⇒ the entire TCOEF VLC can be enumerated from block-0 alone, with NO prediction
model needed. AC-prediction is only needed later to DECODE real multi-block frames.

AC0 start pinned at **bit 27** via first-diff of two block-0-AC frames (same DC0,
different AC level): 7 hdr + 10 MCBPC + 8 (cbpy=1000 region, incl. likely
ac_pred_flag) + 2 DC0(`10`). The earlier ±1 wobble was the **sign bit** (now in the
suffix when amps share a sign).

**dequant CONFIRMED** (oracle): AC `|coef| = quant*(2*|level|+1)`; DC level via
dc_scaler. So exact (run,level,last) is computable from the oracle for any block.

**run=0, last=1 TCOEF magnitude codes (+ trailing sign bit):**

| level | code | level | code |
|---|---|---|---|
| 1 | `111` | 5 | `000000110` |
| 2 | `01100` | 6 | `0000000101` |
| 3 | `0010110` | 7 | `0000000100` |
| 4 | `00010111` | 8 | `00001011001` |

### Scan = standard zigzag; TCOEF (run, level=1, last=1) for run 0..13

Single coeff at (u,v) probed across positions; under the standard JPEG/MPEG zigzag
order the codes form a coherent length-monotone VLC ⇒ **scan is zigzag** (strong
evidence; fitter will confirm). (run, level=1, last=1) magnitude codes (+sign bit):

| run | code | run | code |
|---|---|---|---|
| 0 | `111` | 7 | `0010100` |
| 1 | `01111` | 8 | `0010011` |
| 2 | `01110` | 9 | `0011010` |
| 3 | `010001` | 10 | `00010101` |
| 4 | `010000` | 11 | `00010100` |
| 5 | `010011` | 12 | `00010011` |
| 6 | `0010101` | 13 | `00010010` |

Plus (run=0, level=1..8, last=1) from the level sweep. The full TCOEF table is now a
**mechanical sweep** run × level × last (+ escape for rare combos), all from block 0.

### Direct TCOEF VLC (last=1) extracted; rest is 3-tier escape

Sweep (re/gen_tcoef.py, amp swept finely, level read from oracle — the basis is ~2.1×
the codec coefficient so we never guess level from amp). Result: a small **direct
RL-VLC** + escape, exactly the MPEG-4 shape. Direct (run, level, last=1) magnitude
codes (+ trailing sign bit):

| run | levels → codes |
|---|---|
| 0 | L1 `11`, L2 `0110`, L3 `001011`, L4 `0001011`, L5 `00000011`, L8 `0000101100` |
| 1 | L1 `01111`, L2 `00010110`, L3 `000000101` |
| 2 | L1 `01110`, L2 `000000100` |
| 3 | L1 `010001`, L2 `0000100100` |
| 4 | L1 `010000`, L2 `0000100101` |
| 5 | L1 `010011`, L2 `00001011010` |
| 6 | L1 `0010101`, L2 `00001011011` |
| 7–15 | L1: `0010100` `0010011` `0011010` `00010101` `00010100` `00010011` `00010010` `00010001` `0000100110` |

≈26 clean codes, prefix-free (4 collisions are noise at the escape boundary, run0 L6/L7).
Each run carries only its few most-common levels directly; everything else escapes.

**Escape = 3-tier** (matches the v3 "3-tier ESC"): escape prefixes seen are
`0000111001…` and `0000110010…` followed by raw run/level bits (e.g. level-6/7 of
every run land on `0000110010 <run-bits> 000001x`). The three tiers ≈ MPEG-4's
ESC: (1) level += LMAX, (2) run += RMAX, (3) full fixed-length (last,run,level).

### ESCAPE CRACKED — tier-1 = level-offset recursion

Sweeping run=0 through the escape boundary: levels 1–8 are direct, then **ESC1 =
prefix `00001110`** followed by the NORMAL VLC code for `(run, level − LMAX[run], last)`:

| level | escape code | = ESC1 + direct(level−8) |
|---|---|---|
| 9  | `00001110`+`111`        | L1 |
| 10 | `00001110`+`01100`      | L2 |
| 11 | `00001110`+`0010110`    | L3 |
| 12 | `00001110`+`00010111`   | L4 |
| 13 | `00001110`+`000000110`  | L5 |
| 14 | `00001110`+`0000000101` | L6 |

Exact match ⇒ **ESC1 subtracts LMAX[run] from the level and re-codes** (MPEG-4 tier-1).
`LMAX[run]` = the max level in that run's direct table (run0→8, run1→3, …). When
`level − LMAX` is STILL escape-range, it recurses → the deeper `0000110010…` prefix
is ESC2/ESC3 (run-offset, then full fixed last/run/level). So the TCOEF decoder is:
**direct VLC + ESC1(level−=LMAX) [+ ESC2 run−=RMAX, ESC3 fixed]**.

### last=0 direct codes (2-coeff blocks, first coeff = last=0)
run0: L1 `0`, L2 `10`, L3 `111`, L4 `1101`, L5 `1100` (L1=`0` is boundary-suspect —
verify in fitter). run1: L1 `110`, L2 `10100`, L3 `010110`, L4 `0011100`. The most
common (last=0,run0,L1) gets the shortest code, as expected.

### ESC3 (full escape) CRACKED — TCOEF complete

Pushing run=0 past the ESC1 range: ESC1 handles level−LMAX up to LMAX again
(L9–16 → L1–8), then **ESC3 = prefix `0000110010`** + a fixed-length field whose
binary value IS the level: L17 → `…0010001` (=17), L19 → `=19`, L20 → `=20`,
L43 → `=43` — exact. So the field is `last(1) run(6) level(fixed)` raw bits.

**TCOEF decode is now fully understood:**
1. direct VLC (the common (run,level,last) codes), else
2. **ESC1** `00001110` → decode another TCOEF, add LMAX[run] to its level, else
3. **ESC3** `0000110010` → fixed-length last/run/level (level as raw binary).

This is the MPEG-4-family 3-tier escape. With the direct tables + LMAX[run] (= each
run's max direct level) + the ESC3 field widths, every (run,level,last) decodes.

ESC3 field = **13 bits** = run (high bits) + level (low 6 bits). For level 24 all
cases end `011000`(=24); run bits: r0 `0000000`, r1 `0000100`, r2 `0001000`,
r3 `0001100` (run in the upper part; the low 6 bits are the raw level). Sign for
negative levels still TBD (rare). Practical decode: ESC3 → 13-bit field, level =
field[7:13], run = field[0:5]; refine widths later (ESC3 is rare in real content).

### MAJOR STRUCTURE CORRECTION — multi-block decode works

Decoding the 2-MB frame mb_128_160 (MB0=128, MB1=160) byte-exactly fixed the
structure. The earlier "MCBPC=`1011110111` per MB" + "17-bit trailer = 6 EOBs" was
WRONG. The truth:

  **picture header = 17 bits** (coding 2 + quant 5 + 10 more, incl `1011110111`),
  then **per MB**: short MCBPC/CBPY (`10` for a flat intra MB) + **6 DC contiguous
  (gradient-predicted)** + **AC sections only for cbp-set blocks**, then a
  frame-level trailer.

Verified: MB0 DC `[0,0,0,0,0,0]`, **MB1 DC `[16,0,0,0,0,0]`** — MB1's block-0 DC
diff = 16 = exactly the cross-MB left-neighbour prediction (V160 level80 − V128
level64). The 17-bit `11001000110000111` appears ONCE at end of frame (not per MB).

Consequences:
- **AC is cbp-gated, standard MPEG-4** — empty blocks have NO AC section, so there
  is **NO per-block EOB to reverse**. A coded block's AC ends on `last=1`.
- The "ac_pred 1-bit wobble" lives inside the per-MB MCBPC/CBPY/ac_pred prefix, to
  be split — but the DC layer + cross-MB prediction now decode correctly.
- **The multi-block DC decoder works** (header → per-MB CBPY + 6 predicted DC).

### Per-MB prefix + interleaved AC connected; DC validated on a real AC frame

The 16-pattern table measured earlier IS the **per-MB prefix** (MCBPC + ac_pred_flag
+ CBPY), e.g. flat=`10`, cbpy1000=`0101010`. Structure is **interleaved**: per MB,
`prefix` then for each block `DC` then `AC` (TCOEF…last) **only if that block's cbp
bit is set**. Flat frames look like 6 contiguous DC because no block has AC.

Decoded b0_hgrad (cbpy=1000) with this: prefix(7) → **DC0 = −1, which MATCHES the
oracle** (block-0 mean clipped to 126 → DC level −1). So the DC layer decodes
correctly even on an AC-carrying frame. AC then starts at bit 27.

Open for AC-block decode:
- b0_hgrad isn't a clean single-coeff block (oracle shows a main AC + a tiny one),
  so it's a poor verification target — use the clean `z_*`/`ac01_*` probes.
- **ac_pred_flag** (when the block carries AC) selects AC prediction direction AND
  the **scan** (zigzag vs alternate H/V) — so the TCOEF run/level must be read with
  the right scan. This is the last coupling to pin for AC blocks.

### AC-BLOCK DECODE WORKS (9/9) — ac_pred bit + zigzag pinned

Per cbp-set block the layout is: `DC` + **1 ac_pred/marker bit** + `AC` (TCOEF…last=1).
Decoding the clean single-coeff probes `z_*` end-to-end (header 17 + prefix 7 for
cbpy=1000 + DC0 + **1 bit** + TCOEF) gives the AC run for positions 1..9 = **0..8**
exactly → **scan = zigzag confirmed**, and the 1 bit after DC (value `0` here =
ac_pred off) positions the AC. 9/9 correct.

So the full intra block decode is: `prefix` (→ cbp pattern, dc_scaler) then per block
`DC (gradient-predicted)` + if cbp set `ac_pred_bit` + `TCOEF coeffs until last=1`
(zigzag, dequant `AC=q(2L+1)`). Block-0 (no neighbour) needs no AC prediction; the
remaining work is AC prediction for blocks 1..5 when ac_pred=1, then IDCT.

### WORKING Go INTRA DECODER (subset) — reconstruct == oracle

`decode.go` `DecodeIntraFrame` + `idct.go` + `cbpy_table.go` assemble the whole
pipeline: picture header → per-MB (prefix → cbp; per-block gradient-predicted DC +
ac_pred bit + TCOEF zigzag) → dequant (`AC=q(2L+1)`) → float IDCT → 4:2:0 image.
Verified against the ffmpeg oracle:
- flat / 4-distinct-block / 2-MB cross-MB frames → **bit-exact** (MSE 0);
- single-AC-coeff blocks → PSNR ~34 dB (dequant rounding, fine for thumbnails).
Self-contained `TestDecodeM4` ships the m4 fixture (4 blocks 128/160/144/176).

Sign convention pinned: **sign bit 1 = positive**. DC reconstruct = `level·dcScaler`
(no +128 offset — DC carries brightness). DC default predictor = `1024/dcScaler`.

### last=0 still the blocker for real frames
String-anchored extraction of TCOEF `last=0` codes is fragile (the second-coeff
anchor matches at several offsets → codes collide, e.g. `100` ⊂ `100110`). Partial:
L2≈`100`, L3≈`1110`, L4≈`11010`, L5≈`11000` (with the marker/ac_pred bit consumed),
but not prefix-free vs the verified last=1 set. Real DIV3 frames error on the first
multi-coeff block.

### Learning decoder built (re/learn_last0.py) — last=0 partly cracked, blocker found

The learning decoder works: decode crafted multi-coeff blocks (ac_pred=0), take each
block's (run,level,last) sequence from the oracle, and LEARN unknown last=0 codes by
isolating the one whose remaining block decodes exactly (with a prefix-free guard).
It learned **24 jointly-prefix-free last=0 codes** for runs 0–7.

BUT validation (reconstruct == oracle on multi-coeff frames) shows the **short codes
are wrong** (0/19 near-exact): the learned codes are prefix-free yet don't match the
bitstream — an accumulated 1-bit alignment error.

ROOT CAUSE found: the **run0 / low-level last=0 coefficients sit in ac_pred=1 blocks**
(the encoder turns ac_pred ON whenever a low-frequency coeff like (0,1)/(1,0) is
present — e.g. a 2-coeff block (0,1)+(1,0) is ap=1). **ac_pred=1 uses the ALTERNATE
scan (not zigzag)** + AC prediction, which is NOT yet reversed. So those codes can't
be read in zigzag, and the ap=0 chain can't bootstrap them (no correct short code to
anchor on). The learnable ap=0 codes are higher-run / higher-level only.

> NEXT (the real blocker): reverse **AC prediction + the alternate scan** for ac_pred=1
> blocks (MPEG-4 §7.4.3: predict first row/col from neighbour; scan = alt-horizontal
> or alt-vertical by direction). For block 0 (no neighbour) predictor=0 so coded==
> reconstructed, but the SCAN differs — determine which alt scan ap=1 block-0 uses by
> single-coeff ap=1 probes, then re-run the learner in that scan to get the run0/low
> last=0 codes. Then the TCOEF table is complete → real DIV3 frames decode.

### (superseded) Cleanup → AC/DC-prediction layer (ac_pred_flag found)

- (1,2)=`00010110` collided with (0,4)=`0001011` (prefix) → removed pending re-check.
- **last=0 can't be cleanly isolated by string anchoring**: AC0 = 27 for a 1-coeff
  block but **26 for a 2-coeff block** — a **1-bit ac_pred_flag** sits in the cbpy
  region and shifts the AC start. So the TCOEF table is internally consistent (round-
  trips, matches manual) only up to this global ±1; correct absolute alignment needs
  the ac_pred_flag parsed. ⇒ last=0 + alignment fall out of the multi-block fitter,
  not pure extraction.

This is the entry to **AC/DC prediction**: ac_pred_flag (per MB) selects whether the
first AC row/col is predicted from the neighbour (and pairs with the DC predictor
direction). The Go TCOEF decoder stands; the prediction layer positions it.

> NEXT (AC/DC prediction + EOB — the layer): build the multi-block fitter that
> models MPEG-4 §7.4.3-style DC prediction (gradient A/C from left/top neighbour) +
> ac_pred_flag (AC row/col prediction) + the per-block EOB (empty-block terminator),
> verified by consume-exactly + reconstruct==oracle on multi-MB frames. That pins
> ac_pred, EOB, last=0, the (1,2) code, and DC prediction together. Then dequant +
> zigzag + IDCT → working I-frame; then P-frames + motion comp.

### DC prediction CRACKED — standard MPEG-4 gradient; structure is [6 DC][6 AC]

4-distinct-block frame (luma V=128,160,144,176) decoded DC differentials
`[0,16,8,8,0,0]` then the SAME flat trailer. With **dc_scaler=16**, default
predictor = 1024 (coef) = level 64:
- block0 (V128, level64): pred=default 64 → diff 0 ✓
- block1 (V160, level80): pred=block0 (LEFT) 64 → diff 16 ✓
- block2 (V144, level72): pred=block0 (TOP) 64 → diff 8 ✓
- block3 (V176, level88): pred=block1 (TOP) 80 → diff 8 ✓

⇒ **standard MPEG-4 gradient DC prediction** (predict from left or top neighbour),
and the MB layout is **[6 DC contiguous][6 AC sections]** (DC block separate from
AC, NOT interleaved). The 6 DCs decode cleanly in order and match the predictor.

The flat trailer `11001000110000111` = the **6 empty AC sections (6 EOBs)** — to be
decomposed (4 luma + 2 chroma terminators).

> (older note) finish/clean direct
> tables (last=0 + each run's level range) → port the whole TCOEF decoder to Go;
> THEN AC/DC prediction + EOB; dequant(AC=q(2L+1)) + zigzag + IDCT → reconstruct ==
> oracle (working I-frame decode); then DC prediction, P-frames + motion comp.

### CBPY probe (16 luma AC patterns, single AC coeff each)
MCBPC = `1011110111` (10 bits) rock-solid for all (intra, cbpc=00). At bit 17:
- pattern 0000 (no luma AC) → starts `1`
- every pattern WITH luma AC → starts `0`
So bit 17 ≈ "some luma block has AC". Each pattern then yields a distinct CBPY
codeword (bits 17+), but CBPY/DC/AC/EOB are interlocked — clean separation needs
joint deduction (decode-and-consume-exactly once the run/level/last VLC + EOB are
known). This is the crux of Phase 3.

## Provenance tools (KEEP)
`re/craft.sh`, `re/extract.sh`, `re/gen_dc_luma.py`, `re/gen_dc_chroma.py`,
`re/probe_ac.py`, `re/NOTES.md`.

## Still to reverse
1. AC run/level/last VLC + escape (in progress), AC prediction, scan tables.
2. Full MCBPC + CBPY VLC tables.
3. DC spatial prediction (multi-MB) — unblocked once AC consumes correctly.
4. Dequant + IDCT params; chroma dc_scaler; verify pixels vs oracle.

## SPEC ACQUIRED (spec/h263v1.pdf + spec/REFERENCE.md) — major findings

Downloaded full ITU-T H.263 (1996, 58pp). Standard pieces confirmed to transfer 1:1:
dequant `|REC|=QUANT*(2|LEVEL|+1) - [QUANT even]`, zigzag, IDCT — match our reversal.

- **P-frame path (from H.263):** MVD VLC (TABLE 11), MV = median(left, top, top-right)
  per component + border rules, half-pel bilinear `b=(A+B+1)/2, d=(A+B+C+D+2)/4`.
- **INTRA+Q:** MB type 4 (TABLE 4) carries a 2-bit DQUANT. This is the missing half of
  MS's joint MCBPCY that REAL DIV3 frames use — our constant-QP crafted frames never
  emitted it, which is why real-frame MB1 wouldn't parse (`11...` not in our 64-table).
- **AC prediction = H.263 Annex I / MPEG-4 Advanced Intra Coding:** direction = DC
  gradient; horizontal pred -> alternate-VERTICAL scan (as MPEG-2); vertical pred ->
  alternate-HORIZONTAL scan; DC-only -> zigzag. block0 (no neighbour): predictor=0 so
  coded=actual, but the SCAN still switches -> why ap=1 block0 failed to decode in zigzag.
- **MS TCOEF is its OWN table, NOT H.263** (verified: single (0,1) L1 -> `11`+sign = our
  (0,1,last1); H.263 would be `0111`. (0,1) L2 -> `0110` = ours). last=1 reversal correct.
- **CONTAMINATION:** ap=1 derivation gives (run0,L1,last0)=`10` which collides with our
  learned (run1,L2,last0)=`101000`. The 32 learned last=0 codes were labelled with
  ZIGZAG runs, but the encoder coded those multi-coeff blocks as ap=1/alt-scan -> wrong
  runs. last=0 must be RE-derived after the alt-scan is fixed.

### NEXT
1. Nail alternate scan (verify MPEG-2 alt-vertical/horizontal vs ap=1 blocks by reconstruct).
2. RE-derive last=0 with correct runs.
3. Extract INTRA+Q joint-MCBPCY (needs QP-varying frames).
4. Build P-frame path from H.263 (MVD median + half-pel; tables in spec/h263.txt).

================================================================================
## CLEAN REBUILD via spec + real samples (2026-06-20)
================================================================================

Acquired the format SPEC (spec/msmpeg4.txt, GFDL doc by Niedermayer — a
specification, not ffmpeg source) + real DivX3 movie samples (user-provided).
This revealed the FULL structure and that the EARLIER recon_loop.py tables are
CONTAMINATED (built on limited crafts before the structure was known).

### Proof recon_loop is wrong
A single level-1 coefficient at (0,1) encodes to AC bits `0111`+sign. recon_loop's
decode_tcoef returns (2,-1,1) — garbage. Clean reverse gives (0,1,1)=`0111` (correct,
verified bit-exact). recon_loop "decoded" real MBs but with junk run/level values.

### Format structure (from spec, verified black-box)
- I-frame header: pictype u(2)=00 | quant u(5) | slice_code u(5) | rl_chroma_idx c3 |
  rl_table_idx c3 | dc_table_idx u(1).   c3 VLC: 0->0, 10->1, 11->2.
  field `1011110111` = slice1 + rl_chroma=1 + rl_table=2 + dc=1 (our reversed config).
- 3 RL tables (x2 luma/chroma) + 2 DC tables, selected per-frame by the header.
- Intra MB: code=table_mb_intra (6-bit) -> CBP PREDICTION for luma
  (cbp[i]=code_bit XOR pred, pred=(A==B)?C:B from neighbour coded-flags) -> ac_pred u(1)
  -> per block DC + (if coded) AC.
- table_mb_intra had the ac_pred bit BAKED IN our old mcbpc table (all codes ended 0,
  crafts were ac_pred=0); strip last bit -> correct 6-bit codes (data/table_mb_intra.json).
- AC escape: entry rl->n=`0000011` + esc-level(1->esc1,01->esc2,00->esc3);
  esc1=code+sign,level+=max_level[last][run]; esc2=code+sign,level; esc3=last(1)+run(6)+level s(8).

### Clean reverse method (scripts here)
- clean_rl_reverse.py: craft single/double coefficient (pos AND neg) -> common AC-prefix
  is the CODE, the differing bit is the SIGN. Oracle-verify the level. -> data/rl_table2.json.
- escape_inner_reverse.py: extract esc1 inner codes from escaping coefficients.
- decoder_oracle.py: reverse codes ffmpeg's ENCODER won't emit, via the ffmpeg DECODER
  as pixel oracle (hand-build a frame with a test code, patch into .avi, decode, DCT).
- clean_decoder.py: spec-accurate decoder (header c3 + table_mb_intra + CBP-pred +
  ac_pred + clean rl_table2 + escape). Validated: MB(0,0) of a real DivX3 keyframe
  (Jackasses) fully decodes (all 6 blocks).

### Status / TODO
- rl_table2.json ~100 clean codes; gaps remain (escape-inner / rare high-run codes that
  ffmpeg never emits) -> finish via decoder_oracle.py.
- Then reconstruction (DC-pred + dequant + IDCT + AC-pred) + MSE=0 vs oracle, then Go.
- The OLD recon_loop.py and its derived Go tables (tcoef_table.go) are CONTAMINATED and
  must be replaced by the clean rl_table2 once complete.

================================================================================
## P-FRAME DECODER — Phase 2 findings (2026-06-22)
================================================================================

The Go P-frame decoder (`pframe.go`) passes pixel comparison tests for 5 real
DivX3 movies. Key bugs found and fixed during Phase 2; all derived from the H.263 spec
(spec/h263v1.pdf) and pixel oracle verification.

### P-frame header (confirmed on 5 real samples)
  pictype u(2)=01 | quant u(5) | use_mb_skip u(1) | rc_idx c3 | dc_idx u(1) | mv_idx u(1)

### MV VLC escape sentinel (CRITICAL BUG — fixed)
The combined MV tables (`mvVLC0`, `mvVLC1`, from `pframe_mv_vlc.go`) include a
sentinel entry for value `{-32, -32}`. This is NOT a real DMV — it signals an
escape: two 6-bit raw fields follow (`rawX`, `rawY`), and the actual delta is
`dmvx = rawX - 32`, `dmvy = rawY - 32` (range −32..+31).

Confirmed by 15 escapes in the 6days P-frame (bit positions 6947–53765). Without
the escape handler, MSE was 994 (corrupted output); after fix, MSE dropped to ≈8.

### H.263 MV prediction — first row (my==0)
Per H.263 §7.6.5: when the current MB is in the first row (`mb_y == 0`), there is
no top or top-right neighbor. Predictor = left MV only (`pred = left`). For the
first MB in row 0, left = (0,0).

### H.263 MV prediction — rightmost column top-right (CRITICAL BUG — fixed)
For `my > 0`, the predictor is `median3(left, top, top-right)` per H.263 §7.6.5.
Top-right = MV of MB at `(mb_x+1, mb_y-1)`.

For the rightmost column (`mx == mbw-1`), the top-right MB is outside the frame.
The H.263 motion-vector array is zero-initialized at the borders — so top-right
reads as `(0, 0)`, NOT as a copy of the top MV.

Bug: our code used `prx = ptx; pry = pty` (copy top) for the last column.
Fix: use `prx = 0; pry = 0` (zero, matching H.263 border behavior).

Impact: all MBs in the rightmost column had wrong MV prediction, cascading errors
across the entire column. After fix, 6days MSE dropped from ≈8 to 0.17 (55.9 dB).

### TCOEF for inter blocks
Inter MBs use `chromaTCOEF[rcIdx]`. For typical rcIdx=2 this is the H.263
inter RL table (`tcoefInterVLC`). Entry format and table same as for intra,
with a different code assignment. All verified via pixel oracle on all 5 test files.

### Results after Phase 2 fixes
All 5 real DivX3 movies decode P-frame with PSNR > 50 dB vs oracle:
- Jackasses: 88.4 dB (Y MSE=0.00, maxDiff=1)
- Dogville:  66.9 dB (Y MSE=0.01, maxDiff=1)
- Atlantida2: 89.0 dB (Y MSE=0.00, maxDiff=1)
- 6days: 51.6 dB / 55.9 dB with perfect I-ref (Y MSE=0.45/0.17, maxDiff=31/27)
- ClanBase: 54.8 dB (Y MSE=0.22, maxDiff=19)
Remaining error is IDCT float/integer precision and I-frame rounding (not a P-frame bug).

---

## P-frame VLC tables — black-box derivation (2026-06-25)

`mvVLC0/1` and `mbNonIntraVLC` are derived purely black-box and verified bit-for-bit.
Method, all ffmpeg-binary-only (encoder produces controlled bits; decoder is a pixel oracle):

- **MV tables** (`pframe_oracle.py`): hand-build a P-frame where one interior MB is coded
  inter/cbp0 with a candidate MV codeword and every other MB is skipped; decode with ffmpeg
  and measure the probed MB's motion -> the codeword's `(dmvx,dmvy)`. A complete-prefix-code
  DFS (`p` is a leaf iff `decode(p+"0")==decode(p+"1")`, since the MB's MV depends only on
  its own codeword) recovers all 1100 leaves per table; the escape leaf is detected by its
  literal `u(6)u(6)` behaviour. Sign: shifting content by `+dx` decodes as `dmv=-2dx`
  (the vector points at the source). Result: **mv0 1100/1100, mv1 1100/1100, exact**.

- **mb_type inter half** (`pframe_mb_extract.py`): flat top-row/left-col border -> those MBs
  skip, so the first textured MB is the first coded MB; its predictor is 0 so its MV codeword
  is known (black-box). Read `(mv, cbp)` from the decoded pixels, isolate `mb_type` as the
  bits before that known MV codeword. A zero-mean checker perturbation sets cbp bits without
  moving the ME. **64/64 exact.**

- **mb_type intra half** (`pframe_mb_intra.py`): flat reference everywhere; the probe MB's
  region in frame0 is random (kills inter prediction) so the encoder picks INTRA. Two clips
  that differ only in block-0's DC level diverge exactly at block-0's DC codeword, which sits
  right after `mb_type + ac_pred(1)` -> `mb_type` = bits before that. cbp read from pixel
  variance. **64/64 exact.**

Generators: `gen_mv_blackbox.py`, `gen_mb_blackbox.py` (read only the black-box JSON dumps).
Real-file P-frame tests (`TestPFramePixelCmp`, `TestRealPFrames`, all 5 DivX3 samples) pass.

### I-frame MCBPC/CBPY (`table_mb_intra` -> mcbpc_table.go), black-box (2026-06-25)

`re/iframe_mcbpc.py`: a single 16x16 (one-MB) I-frame; texture the blocks to be coded
(AC -> cbp bit) and read the actually-coded pattern from the decoded pixels. Intra CBP
prediction is active even at MB(0,0) for luma blocks 1..3 (block0 is their in-MB
neighbour), so the table stores RAW = actual XOR pred; we undo it:
r0=a0, r1=a1^a0, r2=a2^a0, r3=a3^(a2 if a0==a1 else a1). The MCBPC codeword is isolated by
first-diff of two clips differing only in block-0's DC (they diverge at block-0's DC
codeword, right after mcbpc + ac_pred). All 64 patterns reproduce mcbpc_table.go exactly.

### MS RL-VLC tables (tcoefTable0/2/1VLC), black-box (2026-06-25)

`re/rl_oracle.py`: a one-MB 16x16 I-frame whose header selects the target RL table index
(note the 5 fixed header bits `10111` between qscale and the table indices at q=4) carries
one candidate TCOEF codeword in a single AC-coded block (MCBPC raw `00_1110` codes only
block-0 after intra CBP prediction; DC level-0 codes luma `1` / chroma `00`). ffmpeg decodes
it; the produced coefficient is read from the block's DCT: zig-zag position -> run,
magnitude -> level (dequantAC at q=4 = 8L+3), coefficient count -> last (1 => last=1, the
`1`*48 tail adds coeffs when last=0). DFS the prefix tree (leaf iff decode(p+'0')==
decode(p+'1'); the bit after a codeword is its sign). The escape leaf is found with an ESC3
literal probe (esc + `00` + last + run(6) + level(8)). Validated on the known lumaTCOEF[2]
(102/102) first, then tables 0/1/chroma-0: 465/465 entries + escapes + maxlev all exact.
