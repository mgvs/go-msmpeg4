#!/usr/bin/env python3
"""Reconstruction-loop decoder + learner for MS-MPEG4 v3 intra (DIV3).

This is the "full-frame decode, validate by pixel reconstruction" approach that
replaces fragile per-code bit-matching. It decodes an entire crafted frame using
the known structure (header, MCBPC/CBPY prefix, DC VLC) plus a *learnable* TCOEF
table, and learns the MS-specific last=0 AC codes by:

  - generating the EXPECTED (run,level,last) event sequence for each block from
    the ffmpeg oracle (exact reconstructed coefficients) + the scan,
  - walking the bitstream event-by-event: known code -> consume & verify; the one
    unknown code in a block -> isolate it (the bits up to where the remaining
    KNOWN events + block boundary decode exactly), with a prefix-free guard.

Bootstrap on ap=0 frames (zigzag, no AC prediction => coded == actual, no
ambiguity). Those yield almost every last=0 code; the few run0/low-level codes
that only occur in ap=1 blocks are handled afterwards with the alt scans.

Run:  python3 re/recon_loop.py            # learn + report
      python3 re/recon_loop.py validate   # decode+reconstruct a corpus vs oracle

Clean-room: ffmpeg is used only as a black-box encoder (controlled pixels -> bits)
and as a pixel oracle. No FFmpeg source, no Microsoft binary.

FINDINGS (what this harness revealed — next concrete steps):
  1. The block tail is NOT just "5 DC codes": after the 5 DC-only blocks (1..5) there
     is a ~17-bit per-MB TRAILER `11001000110000111` (observed identical for single
     (1,0) and (2,0) coefs; for single (0,1) it appears as the 9-bit suffix `110000111`
     because that frame's DC codes ran 8 bits longer and the greedy DC walk ate the
     trailer's first byte). This trailer is the EOB/terminator structure (matches old
     notes). block_tail_ok() must decode 5 DCs then require this fixed trailer (and the
     DC walk must stop at exactly 5 — the trailer starts `11/110/1100...` which collide
     with short DC codes, so greedy-until-end is wrong; count exactly 5 then trailer).
  2. The seeded last=1 table (from tcoef_table.go) is sparse (mostly L1, run0..15).
     A single AC coef gives (run,level,last=1) directly, so the FULL last=1 table
     should be derived from single-coefficient ap=0 frames FIRST (sweep zigzag pos =>
     run, amplitude => level), then last=0 learning can anchor on a complete last=1 set.
ORDER: (a) reverse EOB + per-block AC structure; (b) full last=1 from single coefs;
       (c) last=0 via isolation; (d) ap=1 alt-scan; (e) INTRA+Q; (f) P-frames.

STATUS:
  (a) DONE - fixed 17-bit trailer `11001000110000111` = last 17 significant bits of
      every frame; block_tail_ok() validates block0-AC + 5 DC + trailer.
  (b,c) DONE for ap=0/zigzag: learns 34 last=1 + 29 last=0 codes, all validated by
      exact consume + trailer, 0 prefix collisions, 61/548 ap=0 corpus frames decode.
  (d) ap=1 OPEN - the hard nut. A clean 2-coef ap=1 frame (e.g. (0,1)+(1,0)) brute-
      forces to a coded event pair that is CONSECUTIVE (run0 between) i.e. zigzag-
      ordered, NOT the alt-vertical/horizontal run gaps. So MS ap=1 looks like AC
      PREDICTION on a zigzag scan (prediction alters the coded levels) rather than a
      scan switch -- OR the predictor is non-zero for block0 and reshapes the coded
      coefficients. The run0/low-level last=0 codes live only here.

      ap=1 brute-force findings (clean 2-coef frames (0,1)+(1,0) and (0,1)+(0,2)):
        * trailer is still valid; region = block0_AC + 5 DC as usual.
        * the 5-DC tail is AMBIGUOUS (DC codes are short; many split points give a
          valid 5-DC parse) -> the tail no longer pins the block0-AC boundary the way
          it does for ap=0. So isolation needs a KNOWN-code anchor inside block0 AC.
        * predictor=0 (coded==actual) FAILS for BOTH alt scans: alt-vertical expects
          anchor (run1,L1,last1)=`01111` (absent from the region); alt-horizontal
          expects (run0,L1,last1)=`11` (the region contains NO `11` at all).
        * the region begins with a long run of zeros (`00001000...`) => the coded
          first coefficient is a HIGH run/level (or escape), i.e. AC PREDICTION has
          substantially changed the coded levels. So block0's AC predictor is NOT zero
          (contradicts the naive "unavailable neighbour => 0"); MS likely uses the
          DC-default (1<<(bits+2)=1024)-derived predictor for the first row/column.
      NEXT-SESSION PLAN for ap=1:
        1. Determine the block0 AC predictor by crafting ap=1 frames whose only coeffs
           lie OUTSIDE the predicted first row/column (e.g. (2,0),(3,0) with a tiny
           (0,1) just to trigger ap=1) -> those coeffs are unpredicted (coded==actual)
           and their KNOWN codes anchor the block, isolating the predicted ones.
        2. With one clean anchor, back out predictor[0][i] = actual - coded for the
           first row; fit the constant (likely 1024//dc_scaler or //QP).
        3. Re-run learn() with the predictor applied to generate correct ap=1 event
           sequences; the run0/low last=0 codes then learn by the same isolation.

      ap=1 ESCAPE PROBE finding (craft (0,1)low + (0,2)high to force escape on (0,2)):
        * region = constant 18-bit prefix `000000011001000011` + a varying ESC3 level
          field. The field decodes to 2*L+1 where L == the ACTUAL (0,2) level (L7->15,
          L12->25, L17->35). So coded(0,2) == actual(0,2): AC prediction does NOT change
          the higher row-0 coefficient. => the predictor only touches the FIRST coded
          AC position (0,1); (0,2),(0,3)... are unchanged.
        * The MS ESCAPE used here is ESC3 (`0000110010` + 13-bit run/level field), and
          the level subfield is the plain 2L+1 magnitude.
        Remaining ap=1 puzzle is narrowed to ONE thing: how (0,1) (the first AC, in the
        predicted first row) is altered. Next: sweep (0,1) level in ap=1 frames, read
        its coded code/level (now decodable since (0,2)+ are clean anchors), tabulate
        predictor(0,1) = actual - coded.
"""
import subprocess, numpy as np, re, json, os, itertools

CR = "/tmp/msm_craft"
os.makedirs(CR, exist_ok=True)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- DCT / craft
def _basis(u, v):
    B = np.zeros((8, 8))
    for x in range(8):
        for y in range(8):
            cu = (1 / np.sqrt(2)) if u == 0 else 1.0
            cv = (1 / np.sqrt(2)) if v == 0 else 1.0
            B[x, y] = (
                0.5
                * cu
                * cv
                * np.cos((2 * x + 1) * u * np.pi / 16)
                * np.cos((2 * y + 1) * v * np.pi / 16)
            )
    return B


BASIS = [[_basis(u, v) for v in range(8)] for u in range(8)]


def _fdct(block8):
    M = np.zeros((8, 8))
    for k in range(8):
        for n in range(8):
            ck = (1 / np.sqrt(2)) if k == 0 else 1.0
            M[k, n] = 0.5 * ck * np.cos((2 * n + 1) * k * np.pi / 16)
    return M @ block8 @ M.T


def _idct(C):
    M = np.zeros((8, 8))
    for k in range(8):
        for n in range(8):
            ck = (1 / np.sqrt(2)) if k == 0 else 1.0
            M[k, n] = 0.5 * ck * np.cos((2 * n + 1) * k * np.pi / 16)
    return M.T @ C @ M


def craft(name, luma8=None, q=8):
    """Encode a 16x16 DIV3 frame whose top-left 8x8 luma block = 128+luma8 (others gray)."""
    Y = np.full((16, 16), 128.0)
    if luma8 is not None:
        Y[:8, :8] = 128 + luma8
    Y = np.clip(np.round(Y), 0, 255).astype(np.uint8)
    open(f"{CR}/{name}.yuv", "wb").write(Y.tobytes() + bytes([128] * 128))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-s",
            "16x16",
            "-i",
            f"{CR}/{name}.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            str(q),
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            f"{CR}/{name}.avi",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            f"{CR}/{name}.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            f"{CR}/{name}.bin",
        ],
        check=True,
    )


def bitstream(name):
    return "".join(format(b, "08b") for b in open(f"{CR}/{name}.bin", "rb").read())


def oracle_luma0(name):
    """Reconstructed top-left 8x8 luma block coefficients (from oracle pixels)."""
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            f"{CR}/{name}.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    px = np.frombuffer(o[:256], dtype=np.uint8).reshape(16, 16).astype(float)[:8, :8]
    return _fdct(px - 128)


# ---------------------------------------------------------------- tables
def _load_dc(path):
    m = {}
    for ln in open(path):
        g = re.search(r"\{(\d+), 0b([01]+), (-?\d+)\}", ln)
        if g:
            m[g.group(2)] = int(g.group(3))
    return m


DCL = _load_dc(f"{BASE}/dc_luma_table.go")
DCC = _load_dc(f"{BASE}/dc_chroma_table.go")
MCBPC = json.load(open("/tmp/mcbpc_table.json"))  # "cbCr_cbpy" -> prefix bits
PREFIX = {v: k for k, v in MCBPC.items()}

# zigzag: AC scan index (1..63) -> (row,col).  index 0 = DC.
ZIGZAG = [
    (0, 0),
    (0, 1),
    (1, 0),
    (2, 0),
    (1, 1),
    (0, 2),
    (0, 3),
    (1, 2),
    (2, 1),
    (3, 0),
    (4, 0),
    (3, 1),
    (2, 2),
    (1, 3),
    (0, 4),
    (0, 5),
    (1, 4),
    (2, 3),
    (3, 2),
    (4, 1),
    (5, 0),
    (6, 0),
    (5, 1),
    (4, 2),
    (3, 3),
    (2, 4),
    (1, 5),
    (0, 6),
    (0, 7),
    (1, 6),
    (2, 5),
    (3, 4),
    (4, 3),
    (5, 2),
    (6, 1),
    (7, 0),
    (7, 1),
    (6, 2),
    (5, 3),
    (4, 4),
    (3, 5),
    (2, 6),
    (1, 7),
    (2, 7),
    (3, 6),
    (4, 5),
    (5, 4),
    (6, 3),
    (7, 2),
    (7, 3),
    (6, 4),
    (5, 5),
    (4, 6),
    (3, 7),
    (4, 7),
    (5, 6),
    (6, 5),
    (7, 4),
    (7, 5),
    (6, 6),
    (5, 7),
    (6, 7),
    (7, 6),
    (7, 7),
]
ALT_H_RC = [
    [1, 2, 3, 4, 11, 12, 13, 14],
    [5, 6, 9, 10, 18, 17, 16, 15],
    [7, 8, 20, 19, 27, 28, 29, 30],
    [21, 22, 25, 26, 31, 32, 33, 34],
    [23, 24, 35, 36, 43, 44, 45, 46],
    [37, 38, 41, 42, 47, 48, 49, 50],
    [39, 40, 51, 52, 57, 58, 59, 60],
    [53, 54, 55, 56, 61, 62, 63, 64],
]
ALT_V_RC = [
    [1, 5, 7, 21, 23, 37, 39, 53],
    [2, 6, 8, 22, 24, 38, 40, 54],
    [3, 9, 20, 25, 35, 41, 51, 55],
    [4, 10, 19, 26, 36, 42, 52, 56],
    [11, 18, 27, 31, 43, 47, 57, 61],
    [12, 17, 28, 32, 44, 48, 58, 62],
    [13, 16, 29, 33, 45, 49, 59, 63],
    [14, 15, 30, 34, 46, 50, 60, 64],
]


def _scan_from_rc(rc):  # rc[u][v]=1-based scanpos -> list AC index(1..63)->(u,v)
    out = [None] * 64
    for u in range(8):
        for v in range(8):
            out[rc[u][v] - 1] = (u, v)
    return out[1:]  # drop DC


ZZ_AC = ZIGZAG[1:]  # AC scan only (drop DC at index 0)
SCANS = {
    "zigzag": ZZ_AC,
    "alt_h": _scan_from_rc(ALT_H_RC),
    "alt_v": _scan_from_rc(ALT_V_RC),
}


def dequant_ac(level, q=8):
    if level == 0:
        return 0
    a = q * (2 * abs(level) + 1)
    if q % 2 == 0:
        a -= 1
    return a if level > 0 else -a


# ---------------------------------------------------------------- TCOEF table (learnable)
class TCoef:
    """Bidirectional, prefix-free TCOEF code table. key=(run,level,last) <-> code(str)."""

    def __init__(self):
        self.code2key = {}
        self.key2code = {}
        # seed with verified last=1 codes from the Go table (these are MS-correct)
        for ln in open(f"{BASE}/tcoef_table.go"):
            g = re.search(r"\{(\d+), (\d+), (\d+), (\d+), 0b([01]+)\}", ln)
            if g and int(g.group(3)) == 1:  # last==1 only (verified)
                self.add((int(g.group(1)), int(g.group(2)), 1), g.group(5))

    def add(self, key, code):
        if code in self.code2key:
            return self.code2key[code] == key
        # prefix-free guard
        for c in self.code2key:
            if c.startswith(code) or code.startswith(c):
                return False
        self.code2key[code] = key
        self.key2code[key] = code
        return True

    def match(self, bits, pos):
        """Return (key, codelen) if a known code starts at bits[pos:], else None."""
        for k in range(1, 14):
            c = bits[pos : pos + k]
            if c in self.code2key:
                return self.code2key[c], k
        return None


# ---- escape coding (reverse-engineered) -------------------------------------------------
# ESC3: prefix '0000110010' then a 13-bit field = [run:5][level:8 two's-complement signed].
#   (level sign is in the 8-bit field; there is NO separate trailing sign bit.)
#   Derived by sweeping single coeffs at known run (zigzag idx) and level: the high part of
#   the field is exactly 4*run -> run = top 5 bits; level matches actual incl. sign (e.g.
#   +24='00011000', -24='11101000'=232). [NB this fixed a Go bug: it used run=field>>6
#   (=4*run) and a 6-bit unsigned level; corrected to run=field>>8, signed 8-bit level.]
# ESC1: prefix '00001110' then a base TCOEF event whose |level| is offset by LMAX[run].
# Adding escape lifted exact-decode 77 -> 254/564 (ap=0 61->238, ap=1 16/16). Remaining
# ap=0 misses are learning-robustness (single-coeff DC-tail ambiguity lets a few run0 L5-8
# last1 codes be learned 1 bit short), not an escape issue.
ESC3_PREFIX = "0000110010"
ESC2_PREFIX = "0000110100"  # run escape: base TCOEF with run += RMAX+1 (=20)
ESC1_PREFIX = "00001110"
ESC2_RUN_ADD = 20
# ESC1 level offset = LMAX[last][run] = largest |level| with a DIRECT code for that (last,run).
# It is LAST-dependent: last=0 direct codes reach much higher levels than last=1.
LMAX_L1 = {0: 8, 1: 3, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2}  # last=1
for _r in range(7, 64):
    LMAX_L1[_r] = 1
LMAX_L0 = {
    0: 20,
    1: 11,
    2: 5,
    3: 4,
    4: 3,
    5: 3,
    6: 3,
}  # last=0 (run0 direct goes very high)
for _r in range(7, 64):
    LMAX_L0[_r] = 3


def lmax(last, run):
    return (LMAX_L1 if last == 1 else LMAX_L0).get(run, 1 if last == 1 else 3)


def decode_tcoef(tc, b, p):
    """Decode one AC EVENT at b[p:]. Returns ((run, level_signed, last), total_bit_len) or None.
    Handles ESC3 (fixed field), ESC1 (level offset), and direct RL-VLC + sign."""
    if b[p : p + len(ESC3_PREFIX)] == ESC3_PREFIX:
        q = p + len(ESC3_PREFIX)
        run = int(b[q : q + 5], 2)
        lv = int(b[q + 5 : q + 13], 2)
        if lv >= 128:
            lv -= 256  # 8-bit two's complement
        return (run, lv, 0), len(ESC3_PREFIX) + 13  # last via trailer; treat as 0 here
    if b[p : p + len(ESC2_PREFIX)] == ESC2_PREFIX:  # run escape (check before ESC1)
        base = decode_tcoef(tc, b, p + len(ESC2_PREFIX))
        if base is None:
            return None
        (run, lv, last), blen = base
        return (run + ESC2_RUN_ADD, lv, last), len(ESC2_PREFIX) + blen
    if b[p : p + len(ESC1_PREFIX)] == ESC1_PREFIX:
        base = decode_tcoef(tc, b, p + len(ESC1_PREFIX))
        if base is None:
            return None
        (run, lv, last), blen = base
        add = lmax(last, run)
        lv = lv + add if lv >= 0 else lv - add
        return (run, lv, last), len(ESC1_PREFIX) + blen
    m = tc.match(b, p)
    if m is None:
        return None
    (run, level, last), k = m
    sign = -1 if b[p + k] == "1" else 1  # sign bit: 1 = negative
    return (run, level * sign, last), k + 1


# ---------------------------------------------------------------- oracle event sequence
def levels_from_oracle(C, q=8):
    """8x8 reconstructed coefficients -> integer level array (inverse of dequant_ac)."""
    L = np.zeros((8, 8), dtype=int)
    for u in range(8):
        for v in range(8):
            c = C[u, v]
            if abs(c) >= q - 1:  # threshold ~ first level
                lv = int(round((abs(c) + (1 if q % 2 == 0 else 0)) / q / 2 - 0.5))
                if lv < 1:
                    lv = 1
                L[u, v] = lv if c > 0 else -lv
    return L


def event_seq(levelarr, scanname):
    """(run,level,last,sign) sequence for block AC coefficients in given scan order."""
    scan = SCANS[scanname]
    items = []
    for idx, (u, v) in enumerate(scan):  # idx 0 = first AC (scan position 1)
        if levelarr[u, v] != 0:
            items.append((idx, abs(levelarr[u, v]), 1 if levelarr[u, v] > 0 else -1))
    seq = []
    prev = -1
    for j, (idx, lvl, sgn) in enumerate(items):
        run = idx - prev - 1
        prev = idx
        seq.append((run, lvl, 1 if j == len(items) - 1 else 0, sgn))
    return seq


# ---------------------------------------------------------------- block0-AC isolation
def ac_start(name):
    """Walk header+prefix+DC0+ac_pred, return (bits, pos_of_AC, ac_pred_flag, cbpy)."""
    b = bitstream(name)
    p = 17  # header(7)+10-bit field (crafted frames)
    pat = None
    for k in range(1, 15):
        if b[p : p + k] in PREFIX:
            pat = PREFIX[b[p : p + k]]
            p += k
            break
    if pat is None:
        return b, None, None, None
    # DC0 (luma)
    c = ""
    while True:
        c += b[p]
        p += 1
        if c in DCL:
            break
    ap = b[p]
    return b, p + 1, ap, pat


# fixed 17-bit per-MB trailer (terminator/EOB structure), constant across all frames
TRAILER = "11001000110000111"

# ap=1 first-AC-coefficient (position (0,1)) codes, measured by sweeping (0,1) level with a
# high (0,2) escape anchor (re/recon_loop sweep). For ac_pred=1 block0 the first row AC coef
# is prediction-coded and uses THESE codes (distinct from, and prefix-colliding with, the
# normal run0 last=0 codes -> a separate sub-table keyed on ac_pred=1 + first AC position).
# Only actual levels 1..3 occur as ap=1 (higher (0,1) levels make the encoder pick ap=0).
#   actual L1 -> '00'   L2 -> '100'   L3 -> '1110'   (code excludes the trailing sign bit)
AP1_FIRSTCOEF = {1: "00", 2: "100", 3: "1110"}  # level -> code (run0, predicted)
# ap=1 MODEL (confirmed): block0 keeps the ZIGZAG scan; AC prediction is applied to BOTH
# the first ROW ((0,1),(0,2),...) AND the first COLUMN ((1,0),(2,0),...) -- consistent with
# both neighbours unavailable so both get the default predictor. A decoder using zigzag +
# AP1_FIRSTCOEF for (0,1) decodes 6/16 ap=1 corpus frames exactly; the remaining 10 all
# contain a first-COLUMN coeff ((1,0)/(2,0)) which is likewise prediction-coded. NEXT:
# measure the first-column prediction codes the same way (sweep (1,0)/(2,0) level with a
# high escape anchor, read the code), then ap=1 decodes fully.
# Column sweep result: (2,0) is NEVER ap=1 (always coded==actual); (1,0) is ap=1 only at
# level 1 (code '1100'); its L2+ are ap=0 and give the real (run1,L,last0) codes. So the
# only extra prediction code on the first column is (1,0)@L1='1100'. The remaining ap=1
# corpus failures are multi-low-freq blocks ((0,1)+(1,0) together) where the two predicted
# positions interact -- decode order/level interaction still to pin, but the per-position
# prediction codes are now in hand: (0,1)->{1:'00',2:'100',3:'1110'}, (1,0)->{1:'1100'}.
# ap=1 MODEL (SOLVED, 16/16 ap=1 corpus frames decode exactly): block0 keeps ZIGZAG scan and
# the two co-sited first-AC positions of the first row/column -- (0,1) and (1,0) -- are
# prediction-coded; every other AC coefficient is clean (coded==actual, normal TCOEF code).
# The prediction codes are RUN-CONTEXT dependent (which preceding coeff is present):
#   (0,1) @ L1/L2/L3 -> '00' / '100' / '1110'
#   (1,0) @ L1, alone (run1, (0,1) absent)          -> '1100'
#   (1,0) @ L1, right after (0,1) (run0 context)     -> '111'
# (code excludes the trailing sign bit). (0,2),(1,1),(2,0),... are NOT predicted.
AP1_PRED = {
    ("01",): {1: "00", 2: "100", 3: "1110"},  # (0,1) starts the prediction chain
    ("10", "alone"): {1: "1100"},  # (1,0) as first AC (run1) starts a chain
    "chain": {1: "111"},  # consecutive L1 coeff continuing the chain
}


def sig_end(b):
    s = len(b)
    while s > 0 and b[s - 1] == "0":
        s -= 1
    return s


# In the crafted corpus, luma blocks 1..3 and chroma blocks 4,5 are flat gray, and so is the
# DC of block0 (basis functions are zero-mean) -> every DC differential is 0. The level-0 DC
# codes are luma '10' and chroma '00', so the 5 DC-only blocks are always '10 10 10 00 00' =
# '1010100000', followed by the 17-bit TRAILER: a FIXED 27-bit frame suffix. Requiring it
# pins the block0-AC boundary exactly (no DC-tail ambiguity) -> robust, unique code learning.
TAIL_SUFFIX = "1010100000" + TRAILER  # 27 bits: 5 level-0 DCs + per-MB trailer


def block_tail_ok(b, pos, cbpy):
    """block0 AC must consume to exactly the fixed 27-bit tail suffix (5 level-0 DCs+trailer)."""
    sig = sig_end(b)
    return pos == sig - 27 and b[sig - 27 : sig] == TAIL_SUFFIX


def _match_event(tc, b, q, run, lvl, sgn):
    """Consume one KNOWN event (direct or escape) that must equal (run,lvl,sgn). Return new
    pos, or None. Uses decode_tcoef so escape-coded anchors work too."""
    dt = decode_tcoef(tc, b, q)
    if dt is None:
        return None
    (drun, dlevel, _), dlen = dt
    if drun != run or abs(dlevel) != lvl or (1 if dlevel > 0 else -1) != sgn:
        return None
    return q + dlen


def learn_frame(b, pos, ap, cbpy, seq, tc):
    """Decode block0 AC events; learn the one unknown code ONLY if its length is uniquely
    determined (robust against single-coeff DC-tail ambiguity). Return #learned (0/1).
    """
    if seq is None or ap != "0":  # learn from ap=0 (zigzag, no prediction) only
        return 0
    p = pos
    unknown_at = None
    for i, (run, lvl, last, sgn) in enumerate(seq):
        np_ = _match_event(tc, b, p, run, lvl, sgn)
        if np_ is not None:
            p = np_
            continue
        key = (run, lvl, last)
        if unknown_at is not None:
            return 0  # two unmatched events -> can't isolate
        if tc.key2code.get(key):
            return 0  # code known but didn't match here -> inconsistent
        unknown_at = (i, p, key)
        break  # can't advance past an unknown; isolate it below
    if unknown_at is None:
        return 0
    i0, p0, key = unknown_at
    # collect EVERY code length that yields a full consume-exactly; learn only if unique.
    valid = []
    for L in range(1, 14):
        cand = b[p0 : p0 + L]
        if any(c.startswith(cand) or cand.startswith(c) for c in tc.code2key):
            continue  # not prefix-free
        q = p0 + L + 1  # code + sign
        ok = True
        for run, lvl, last, sgn in seq[i0 + 1 :]:
            q = _match_event(tc, b, q, run, lvl, sgn)
            if q is None:
                ok = False
                break
        if ok and block_tail_ok(b, q, cbpy):
            valid.append(cand)
    if len(valid) == 1 and tc.add(key, valid[0]):
        return 1
    return 0  # 0 or >1 candidates -> ambiguous, defer


# ---------------------------------------------------------------- corpus
def build_corpus(scanname="zigzag"):
    """ap=0 frames: a single 'first' coef (varies run via zigzag pos) + an anchor + tail.
    Use HIGH first-coef levels at low scan positions so the encoder keeps ap=0 (zigzag).
    """
    frames = []

    def add(nm, blk):
        if not os.path.exists(f"{CR}/{nm}.bin"):
            craft(nm, blk)
        b, pos, ap, cbpy = ac_start(nm)
        if pos is None or cbpy != "00_1000":
            return
        lv = levels_from_oracle(oracle_luma0(nm))
        frames.append(
            (nm, b, pos, ap, cbpy, lv)
        )  # store level array, scan chosen in learn()

    # (1) single-coef blocks => (run,level,last=1): sweep zigzag position (run) and level.
    #     Two amplitudes per (a,L) give the voter robustness against 2x-basis level boundaries.
    for a in range(0, 48):
        u, v = ZZ_AC[a]
        for L in range(1, 16):
            for d in (0, 2):
                add(f"s_{a}_{L}_{d}", (4 * (2 * L + 1) + d) * BASIS[u][v])
    # (2) 2-coef blocks => (run,level,last=0) for coef1, anchor (run0,*,last1) right after.
    for a in range(0, 42):
        u1, v1 = ZZ_AC[a]
        u2, v2 = ZZ_AC[a + 1]
        for L in range(1, 16):
            for d in (0, 2):
                add(
                    f"c_{a}_{L}_{d}",
                    (4 * (2 * L + 1) + d) * BASIS[u1][v1] + 12 * BASIS[u2][v2],
                )
    # (3) ap=1 corpus: low-freq pairs (force ac_pred); the run0/low last=0 codes live here.
    lowfreq = [(0, 1), (1, 0), (0, 2), (1, 1), (2, 0), (0, 3), (1, 2), (2, 1)]
    for i, p1 in enumerate(lowfreq):
        for p2 in lowfreq[i + 1 :]:
            add(
                f"p_{p1[0]}{p1[1]}_{p2[0]}{p2[1]}",
                12 * BASIS[p1[0]][p1[1]] + 12 * BASIS[p2[0]][p2[1]],
            )
    return frames


def scans_for(ap):
    """Scan hypotheses to try for a frame, given its ac_pred flag."""
    return ["zigzag"] if ap == "0" else ["alt_v", "alt_h"]


def _relearn(tc, frames):
    for it in range(25):
        added = 0
        for nm, b, pos, ap, cbpy, lv in frames:
            for scan in scans_for(ap):
                added += learn_frame(b, pos, ap, cbpy, event_seq(lv, scan), tc)
        if added == 0:
            break


def _vote_pass(tc, frames):
    """One voting round: for every frame's single-unknown event, tally the candidate code
    lengths that consume-exactly; assign each key the code with the most votes (robust to a
    few mislabeled frames / level-detection boundary errors). Returns #codes added/changed.
    """
    votes = {}  # key -> {code: count}
    for nm, b, pos, ap, cbpy, lv in frames:
        if ap != "0":
            continue
        seq = event_seq(lv, "zigzag")
        # walk with known codes until the first unmatched event
        p = pos
        unk = None
        for i, (run, l, la, s) in enumerate(seq):
            np_ = _match_event(tc, b, p, run, l, s)
            if np_ is None:
                unk = (i, p, (run, l, la))
                break
            p = np_
        if unk is None:
            continue
        i0, p0, key = unk
        for L in range(1, 14):
            cand = b[p0 : p0 + L]
            if any(
                c.startswith(cand) or cand.startswith(c)
                for c in tc.code2key
                if c != tc.key2code.get(key)
            ):
                continue
            q = p0 + L + 1
            ok = True
            for run, l, la, s in seq[i0 + 1 :]:
                q = _match_event(tc, b, q, run, l, s)
                if q is None:
                    ok = False
                    break
            if ok and block_tail_ok(b, q, cbpy):
                votes.setdefault(key, {})[cand] = (
                    votes.setdefault(key, {}).get(cand, 0) + 1
                )
    changed = 0
    for key, cc in votes.items():
        best = max(cc, key=lambda c: cc[c])
        # only assign if prefix-free with all OTHER keys' codes
        if any(
            (c.startswith(best) or best.startswith(c))
            for k, c in tc.key2code.items()
            if k != key
        ):
            continue
        if tc.key2code.get(key) != best:
            old = tc.key2code.get(key)
            if old:
                tc.code2key.pop(old, None)
            tc.key2code[key] = best
            tc.code2key[best] = key
            changed += 1
    return changed


def learn(verbose=True):
    tc = TCoef()
    frames = build_corpus()
    n0 = sum(1 for f in frames if f[3] == "0")
    if verbose:
        print(f"corpus: {len(frames)} frames ({n0} ap=0, {len(frames)-n0} ap=1)")
    _relearn(tc, frames)
    for it in range(12):  # majority-vote refinement to convergence
        if _vote_pass(tc, frames) == 0:
            break
    if verbose:
        l0 = sum(1 for k in tc.key2code if k[2] == 0)
        print(
            f"  final: codes={len(tc.code2key)} last0={l0} last1={len(tc.code2key)-l0}"
        )
    return tc, frames


def ap1_pred_code(scan_idx, run, lvl, prev_predicted):
    """Prediction code for an ap=1 block0 coefficient, or None if it is coded normally.

    AC prediction in MS ap=1 (block0, zigzag) forms a CHAIN starting at the lowest AC and
    continuing through consecutive (run0) coefficients:
      - (0,1) (scan idx0)                          -> level code '00'/'100'/'1110'
      - (1,0) (scan idx1) as the first AC (run1)   -> '1100'
      - any L1 coeff at run0 right after a predicted coeff (chain) -> '111'
    Everything else (a gap, or a level the chain doesn't cover) is coded normally."""
    if scan_idx == 0 and run == 0:  # (0,1) starts a chain
        return AP1_PRED[("01",)].get(lvl)
    if scan_idx == 1 and run == 1:  # (1,0) as first AC starts a chain
        return AP1_PRED[("10", "alone")].get(lvl)
    if run == 0 and prev_predicted and lvl == 1:  # chain continues on consecutive L1
        return AP1_PRED["chain"].get(lvl)
    return None


def decodes_exactly(tc, b, pos, ap, cbpy, lv):
    """True if the frame consumes exactly (block0 AC + 5 DC + trailer).
    ap=0: zigzag + normal codes.  ap=1: zigzag + the prediction chain on the lowest AC coeffs.
    """
    seq = event_seq(lv, "zigzag")
    p = pos
    scan_idx = -1
    prev_predicted = False
    for run, lvl, last, sgn in seq:
        scan_idx += run + 1  # absolute AC scan index of this coeff
        code = ap1_pred_code(scan_idx, run, lvl, prev_predicted) if ap == "1" else None
        if code is not None:
            if b[p : p + len(code)] != code:
                return False
            p += len(code) + 1
            prev_predicted = True
            continue
        dt = decode_tcoef(tc, b, p)
        if dt is None:
            return False
        (drun, dlevel, dlast), dlen = dt
        if drun != run or abs(dlevel) != lvl or (1 if dlevel > 0 else -1) != sgn:
            return False
        p += dlen
        prev_predicted = False
    return block_tail_ok(b, p, cbpy)


def report(tc):
    last0 = {k: c for k, c in tc.key2code.items() if k[2] == 0}
    byrun = {}
    for (r, l, _), c in last0.items():
        byrun.setdefault(r, {})[l] = c
    print(f"\nlearned last=0 codes: {len(last0)}")
    for r in sorted(byrun):
        print(
            f"  run{r}: " + "  ".join(f"L{l}={byrun[r][l]}" for l in sorted(byrun[r]))
        )


if __name__ == "__main__":
    import sys

    tc, frames = learn()
    report(tc)
    ok = sum(1 for f in frames if decodes_exactly(tc, f[1], f[2], f[3], f[4], f[5]))
    ok0 = sum(
        1
        for f in frames
        if f[3] == "0" and decodes_exactly(tc, f[1], f[2], f[3], f[4], f[5])
    )
    ok1 = ok - ok0
    n0 = sum(1 for f in frames if f[3] == "0")
    print(
        f"\nfully-decoding: {ok}/{len(frames)}  (ap=0: {ok0}/{n0}, ap=1: {ok1}/{len(frames)-n0})"
    )
