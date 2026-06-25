# WMV2 — reverse-engineering notes

## I-frames — DONE (0.1.0)
`DecodeIntraFrameWMV2` reuses the WMV1 intra body (`wmv1IntraBody`). See `wmv2_probe.py`.

## P-frames — bit-exact (except table 0/2 last codes, ABT, J-frames)
### P-frame header — DECODED (`wmv2_pframe.py`)
`ptype(1)=1 | qscale(5) | parse_mb_skip[skip_type(2) + skip bits] | cbp_index(decode012) |
mspel(1, if mspel_bit) | abt[per_mb_abt = bit^1; abt_type = decode012 if !per_mb_abt] |
per_mb_rl(1, if per_mb_rl_bit) | rl(decode012) | dc(1) | mv(1)`. no_rounding ^= 1 each P-frame.
- parse_mb_skip: skip_type 00=NONE (all coded) 01=MPEG (bit/MB) 10=ROW 11=COL.

### Deriving the 3 mb_non_intra VLCs (the inter MB-type tables) — ENCODER-ORACLE WORKS
**Decoder-oracle** (`wmv2_mb_oracle.py`) — SHELVED: classifying cbp from pixels is unstable
(residual shifts by 1 bit → leaf condition is false). Same problem as v3.

**Encoder-oracle** (`wmv2_mb_extract.py`) — WORKS. Keys:
- The encoder ALWAYS uses skip_type=NONE (all MBs coded), so **probe = MB0** (its mb_type at the start
  of the MB layer). `top_left_mv_flag=0` in the encoder → wmv2_pred_motion never reads a bit → with
  per_mb_rl=0 / abt_type=0 (read from the header) the mb_type comes first → the MV code is a clean anchor.
- `parse_header` parses the WMV2 P-header → mb_layer_start + cbp_index → **table index**
  (`map[(q>10)+(q>20)][cbp_index]`). Sweeping qscale covers all 3 tables.
- Only the probe MB differs from the ref (2px shift → MV=(4,4), even → no mspel); a per-block checker
  gives cbp from residual = decoded − MC(ref, mv). mb_type = `seg.find(mvcode)`. Majority vote.

JSON: `/tmp/wmv2_mbx/mb_inter_t{0,1,2}.json`.

### Intra half (another 64 codes × 3 tables) — METHOD READY
First-diff (like v3 `pframe_mb_intra.py`): probe MB = MB0 = noise (→ encoder picks intra), block0 is the
anchor with a different DC between two clips → they diverge on the DC code after mb_type + ac_pred(1).
The 1-bit ac_pred ambiguity is resolved by prefix-disjointness from the inter codes.

### Result of deriving the 3 tables (encoder-oracle) — 377/384 codes
- **table 2: 128/128** (inter 64 + intra 64), combined prefix-free. Kraft = 0.996 → 1-2 codes slightly
  too long, to check.
- **table 1: structurally clean** — Kraft 0.9053 + 3 free leaves `0111`, `10110`, `1010100000` = exactly
  1.0. Remaining work was to assign inter cbp 28/30/60 to those 3 leaves (decoder-verify which is which).
- **table 0: 124 codes but Kraft = 1.0** → 4 codes are TOO SHORT (they absorbed the slots of 4 missing
  codes). Find the 4 wrong ones and fix them.
Scripts: wmv2_mb_extract.py (inter), wmv2_mb_intra.py (intra), wmv2_mb_resolve.py (resolve by votes).

**KEY VALIDATION: the Kraft sum == 1.0 for a complete table.** Pairwise prefix-free / bijective checks
do NOT catch length errors — Kraft does. The encoder-oracle never emits non-optimal codes (~7 missing) +
gives rare length errors from noisy cbp reads; finish with decoder-verify on the free leaves.

### Filling the last 7 codes — METHODOLOGICAL WALL (early attempts)
Five methods tried, all hit edge cases:
1. single-MB encoder — never emits non-optimal codes (picks the cheapest table for a cbp).
2. multi-MB encoder (force tables with fillers) — the MB0 anchor `seg.find(mvcode)` is unreliable (with
   fillers it jumps to a neighbouring MB's MV); produces spurious `1010…` codes (collisions).
3. decoder-verify (classify) — cbp from pixels unstable (AC overflow from pad bits).
4. free-leaf — known structurally from Kraft, but the leaf↔cbp binding still needs verification.
5. splice-verify — splice `header` + `leaf L` + `real cbp tail C`; the patched P-packet was invalid →
   ffmpeg dropped the P-frame.

The last 7 codes were **encoder-unobservable** with these methods. **Best path: the full P-decoder
itself as the validator** — build the decoder (mspel + assembly) and let a wrong/missing code desync a
specific MB on a real WMV2 P-frame, then fix it end-to-end.

## ★ table 1 → 128/128 (from real WMV2 m4/m5 + ffmpeg oracle) (2026-06-25)
Real MS-WMV2 samples (m4.wmv — table 0 failures; m5.wmv — table 1+2 failures) have abt=1, jtype=1 in
extradata (ffmpeg never emits those). Derivation (decodeWMV2MBType hook: free-leaf code = dead-end
prefix; sweep the intra/cbp symbol; verify the MB against ffmpeg bit-for-bit, iteratively):
**table 1 CLOSED — 3 codes: `0111`→cbp60, `10110`→cbp28, `1010100000`→cbp30 (all inter), verified
bit-for-bit.** Recorded in wmv2_pframe_tables.go.

## ★ no_rounding FIXED (2026-06-25)
The no_rounding bug (my WMV2-P used =0 always; it toggles per P) is FIXED: Decoder.noRound toggles each
P-frame (init=1 after I), threaded through DecodePFrameWMV2 / mcFill / mspel* / avg8 / chroma
(rnd = noRound?0:1). Verified: ffmpeg half-pel multi-P 6 frames +Inf (TestWMV2NoRoundMultiP); m5.wmv
40/40 bit-exact (was 8/60). Universal (flipflop=1 for both ffmpeg AND MS).

## read-limit fix + table 0 is COMPLETE + ms-pel/loop verified (2026-06-26)
Kraft analysis straight from the Go maps: **table 0 = 124 codes, Kraft=1.0, prefix-free, maxLen=21 →
STRUCTURALLY COMPLETE and correct** (not "missing codes"!). The bug was a read-limit: decodeWMV2MBType
read 20 bits but table 0 has a 21-bit code → it never matched. FIX: `for range 22` (> maxLen of all
tables: t0=21, t1=20, t2=19). table 1 = 128, Kraft=1.0. **table 2 = 128, Kraft=0.995968 (<1) → genuinely
missing ~1-2 codes** (free leaves at lengths ~8, 13). The m4/m5 mb_type failures are END-OF-BUFFER reads
(0-bits past the packet) = DRIFT from a wrong symbol / ms-pel earlier in the frame.

**ms-pel filter VERIFIED correct** (disproved the "ms-pel is buggy" hypothesis): a 120-frame scan of m5
found no per-block ms-pel mismatch; the first failures (m4@4, m5@135) are both mspel=FALSE after a long
bit-exact run → a table problem, not ms-pel. **Loop filter is correct on fully-decoded frames** (m5
decodes 135 P-frames bit-exact; a skip-aware variant is unneeded — those frames have no skipped MBs).

## table 0 / 2 rebuild — does NOT converge (deferred)
Even with the now-reliable oracle (m5 bit-exact), re-deriving table 0/2 still fails: a rebuild harness
with interior-block comparison (the loop filter only touches the 2 pixels at block edges, so rows/cols
2-5 are clean) still gets stuck — on the offending MB no mb_type symbol matches even the interior →
the divergence is in the MV/residual/intra-pred of that MB, or the ref chain breaks in a cascade.
A tolerant match yields spurious fixes (Kraft > 1.0). This is a deep, non-converging RE problem; a clean
solution would need a global constraint-solve across many MBs, not point derivation.

**SUMMARY (WMV2, after many passes): the STRUCTURE is correct and verified** — no_rounding ✅,
read-limit (22) ✅, ms-pel ✅, loop filter ✅, table 1 = 128 ✅. table 0 = 124 Kraft=1.0 (structurally
complete), table 2 = 128 Kraft=0.996 (~1-2 missing). Real m4/m5 decode in long bit-exact runs (m5: 135
P-frames, m4: 4) before drifting on a few wrong/missing codes in table 0/2. Remaining: those few
codes (point derivation does not converge), plus ABT / J-frames (the samples have the flags but the
first few hundred frames don't use them).
