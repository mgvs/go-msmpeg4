"""rl_oracle.py — decoder-oracle for the MS-MPEG4 v3 RL-VLC tables (tcoefTable0/1/2VLC =
lumaTCOEF[0], lumaTCOEF[1], chromaTCOEF[0]). Black-box: ffmpeg DECODER only.

We hand-build a one-MB 16x16 I-frame whose picture header selects the target RL table
index, with exactly one AC-coded block carrying a candidate TCOEF codeword. ffmpeg decodes
it; we DCT the probed block to read the produced coefficient: zig-zag position -> run,
magnitude -> level, sign -> sign; whether the block holds one AC coeff or more -> last.
Walking the VLC prefix tree (leaf iff decode(p+'0')==decode(p+'1')) recovers every entry.

Black-box constants used (all themselves black-box-derived earlier): the picture-header
layout, the MCBPC codeword that codes only block-0 (luma) / block-4 (Cb) after intra CBP
prediction, and the dc_table-0 level-0 DC codewords (luma '1', chroma '00').
"""
import os
import subprocess, os, json
import numpy as np

W, H = 16, 16
Q = 4
TMP = "/tmp/rl_oracle"
os.makedirs(TMP, exist_ok=True)
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ZZ = [(0,0),(0,1),(1,0),(2,0),(1,1),(0,2),(0,3),(1,2),(2,1),(3,0),
      (4,0),(3,1),(2,2),(1,3),(0,4),(0,5),(1,4),(2,3),(3,2),(4,1),
      (5,0),(6,0),(5,1),(4,2),(3,3),(2,4),(1,5),(0,6),(0,7),(1,6),
      (2,5),(3,4),(4,3),(5,2),(6,1),(7,0),(7,1),(6,2),(5,3),(4,4),
      (3,5),(2,6),(1,7),(2,7),(3,6),(4,5),(5,4),(6,3),(7,2),(7,3),
      (6,4),(5,5),(4,6),(3,7),(4,7),(5,6),(6,5),(7,4),(7,5),(6,6),
      (5,7),(6,7),(7,6),(7,7)]
UV_TO_K = {uv: k for k, uv in enumerate(ZZ)}

# 8x8 orthonormal DCT
M = np.array([[0.5 * (1/np.sqrt(2) if k == 0 else 1) * np.cos((2*n+1)*k*np.pi/16)
               for n in range(8)] for k in range(8)])

C3 = {0: "0", 1: "10", 2: "11"}
MCBPC_LUMA0 = "010101"   # raw 00_1110 -> actual block0 luma only
MCBPC_CB = "01001"       # raw 10_0000 -> Cb only
DC0_L = "1"              # dc_table0 luma level 0
DC0_C = "00"             # dc_table0 chroma level 0


def build_host():
    # any 16x16 textured I-frame, just to host the bytes
    import random
    rng = random.Random(3)
    raw = bytes(rng.randrange(40, 216) for _ in range(W*H)) + bytes([128])*(2*(W//2)*(H//2))
    open(f"{TMP}/h.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","yuv420p","-s",
                    f"{W}x{H}","-i",f"{TMP}/h.yuv","-c:v","msmpeg4","-qscale:v",str(Q),
                    "-frames:v","1","-vtag","DIV3",f"{TMP}/h.avi"],check=True)
    sizes=[int(x) for x in subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
           "-show_entries","packet=size","-of","csv=p=0",f"{TMP}/h.avi"],
           capture_output=True,text=True).stdout.split()]
    data=subprocess.run(["ffmpeg","-v","error","-i",f"{TMP}/h.avi","-map","0:v:0","-c","copy",
                         "-f","data","-"],capture_output=True).stdout
    pb=data[:sizes[0]]
    avi=bytearray(open(f"{TMP}/h.avi","rb").read())
    off=bytes(avi).find(pb)
    return avi, off, len(pb)

HOST, POFF, PLEN = build_host()


# 5 fixed picture-header bits between qscale and the table indices (constant at q=4,
# verified content-independent over many encodes).
REMAIN = "10111"


def header(rl_chroma, rl_table, dc_idx=0):
    return "00" + format(Q, "05b") + REMAIN + C3[rl_chroma] + C3[rl_table] + str(dc_idx)


def build_luma(rl_table, ac_bits, tail):
    bits = header(1, rl_table) + MCBPC_LUMA0 + "0" + DC0_L + ac_bits + tail
    return bits


def build_chroma(rl_chroma, ac_bits, tail):
    # probe block4 (Cb): luma blocks 0..3 DC-only, then Cb DC + AC, then Cr DC
    bits = header(rl_chroma, 2) + MCBPC_CB + "0" + DC0_L*4 + DC0_C + ac_bits + tail
    return bits


def decode_block(bits, is_chroma):
    while len(bits) % 8:
        bits += "1"
    b = bytearray(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
    if len(b) < PLEN:
        b += bytes(PLEN - len(b))
    avi = bytearray(HOST)
    avi[POFF:POFF+PLEN] = bytes(b[:PLEN])
    open(f"{TMP}/t.avi","wb").write(avi)
    out=subprocess.run(["ffmpeg","-v","error","-i",f"{TMP}/t.avi","-f","rawvideo","-pix_fmt",
                        "yuv420p","-"],capture_output=True).stdout
    fsz=W*H*3//2
    if len(out) < fsz:
        return None
    if not is_chroma:
        blk = np.frombuffer(out[:64], np.uint8).reshape(8, 8)  # block0 = top-left 8x8 of Y
        # careful: Y is 16x16, top-left 8x8 = rows0-7 cols0-7
        y = np.frombuffer(out[:W*H], np.uint8).reshape(H, W).astype(float)
        blk = y[:8, :8]
    else:
        base = W*H
        cb = np.frombuffer(out[base:base+(W//2)*(H//2)], np.uint8).reshape(H//2, W//2).astype(float)
        blk = cb[:8, :8]
    F = M @ (blk - blk.mean()) @ M.T
    return F


def read_coeffs(F, thr=4.0):
    """significant AC coeffs as list of (k_zigzag, value), sorted by k."""
    out = []
    for k in range(1, 64):
        u, v = ZZ[k]
        if abs(F[u, v]) > thr:
            out.append((k, F[u, v]))
    out.sort()
    return out


def level_from(mag):
    # dequantAC(L,q=4) = 4*(2L+1)-1 = 8L+3 ; invert
    return max(1, round((abs(mag) - 3) / 8))


def probe(ac_bits, rl_table, is_chroma, tail="1"*48):
    build = build_chroma if is_chroma else build_luma
    F = decode_block(build(rl_table, ac_bits, tail), is_chroma)
    if F is None:
        return None
    cs = read_coeffs(F)
    if not cs:
        return None
    k0, val0 = cs[0]
    run = k0 - 1
    level = level_from(val0)
    sign = -1 if val0 < 0 else 1
    last = 1 if len(cs) == 1 else 0
    return (run, level, last, sign, len(cs))


def load_existing(varname):
    import re
    txt = open(f"{PKG}/tcoef_tables_extra.go").read() if "Table" in varname else open(f"{PKG}/tcoef_table.go").read()
    m = re.search(rf'var {varname} = \[\]tcoefCode\{{(.*?)\n\}}', txt, re.DOTALL)
    d = {}
    for run, level, last, length, code in re.findall(
            r'\{\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*0b([01]+)\s*\}', m.group(1)):
        d[code.zfill(int(length))] = (int(run), int(level), int(last))
    return d


def value(ac_bits, rl_table, is_chroma):
    r = probe(ac_bits, rl_table, is_chroma)
    if r is None:
        return None
    return (r[0], r[1], r[2])  # run, level, last (ignore sign)


def is_escape(p, rl_table, is_chroma):
    # ESC mode 3: esc + "00" + last(1) + run(6) + level(8 signed). Test two literals.
    for R, L in ((5, 20), (11, 14)):
        ac = p + "00" + "0" + format(R, "06b") + format(L, "08b")
        r = probe(ac, rl_table, is_chroma, tail="1" * 40)
        if r is None or (r[0], r[1]) != (R, L):
            return False
    return True


def walk_table(rl_table, is_chroma, maxlen=16, log_every=64):
    leaves = {}      # code(str) -> (run, level, last)
    esc = None
    stack = [""]
    seen = 0
    while stack:
        p = stack.pop()
        if len(p) >= maxlen:
            continue
        va = value(p + "0", rl_table, is_chroma)
        vb = value(p + "1", rl_table, is_chroma)
        seen += 1
        if seen % log_every == 0:
            print(f"  [tbl{rl_table}{'c' if is_chroma else ''}] visited={seen} leaves={len(leaves)} stack={len(stack)} depth={len(p)}", flush=True)
        if va is not None and va == vb:
            leaves[p] = va
            continue
        if len(p) >= 5 and esc is None and is_escape(p, rl_table, is_chroma):
            esc = p
            continue
        stack.append(p + "1")
        stack.append(p + "0")
    return leaves, esc


if __name__ == "__main__":
    import sys
    spec = {"2": ("tcoefLumaVLC", 2, False), "0": ("tcoefTable0VLC", 0, False),
            "1": ("tcoefTable2VLC", 1, False), "c0": ("tcoefTable1VLC", 0, True)}
    key = sys.argv[1]
    varname, idx, is_chroma = spec[key]
    EX = load_existing(varname)
    import time
    t0 = time.time()
    leaves, esc = walk_table(idx, is_chroma)
    dt = time.time() - t0
    print(f"\n{varname}: {len(leaves)} leaves, esc={esc} in {dt:.0f}s (existing {len(EX)})")
    ok = bad = miss = extra = 0
    fails = []
    for code, v in leaves.items():
        ev = EX.get(code)
        if ev == v:
            ok += 1
        else:
            bad += 1
            if len(fails) < 30:
                fails.append((code, v, ev))
    for code in EX:
        if code not in leaves:
            miss += 1
    print(f"vs existing: match={ok} mismatch={bad} only-in-existing={miss}")
    for c, v, e in fails:
        print(f"  {c}: blackbox={v} existing={e}")
    json.dump({"esc": esc, "entries": {c: list(v) for c, v in leaves.items()}},
              open(f"{TMP}/{varname}.json", "w"))
