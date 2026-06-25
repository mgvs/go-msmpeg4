"""pframe_mb_oracle.py — decoder-oracle for the MS-MPEG4 v3 table_mb_non_intra VLC.

Black-box: ffmpeg DECODER only (pixel oracle) + black-box-recovered P-frame header
layout. No ffmpeg source, no VLC table read.

We hand-build a P-frame in which one interior MB carries a candidate mb_type codeword;
all other MBs are skipped. Decoding tells us, from pixels, whether that MB is intra or
inter and which of its 6 blocks are coded (cbp). Because (intra,cbp) is fixed by the
mb_type codeword alone (independent of the MV/DC/AC bits that follow), a candidate prefix
p is a complete codeword iff classify(p+'0'+pad) == classify(p+'1'+pad); we DFS the
binary prefix tree to recover all 128 leaves.

Reference uses smooth gradients in Y, Cb and Cr so inter blocks (motion-compensated
copies of the reference) are easy to tell from intra blocks (DC/AC synthesised).
"""
import os
import subprocess, os, random
import numpy as np

W, H = 96, 80
MBW, MBH = W // 16, H // 16
NMB = MBW * MBH
PROBE_MB = (MBH // 2) * MBW + (MBW // 2)
PR_R = (PROBE_MB // MBW) * 16
PR_C = (PROBE_MB % MBW) * 16
TMP = "/tmp/pf_mb"
os.makedirs(TMP, exist_ok=True)


def smooth(seed, w, h, lo=40, hi=216):
    rng = random.Random(seed)
    lw, lh = w // 8 + 2, h // 8 + 2
    low = np.array([[rng.randrange(lo, hi) for _ in range(lw)] for _ in range(lh)], np.float64)
    yi = np.linspace(0, lh - 1.001, h); xi = np.linspace(0, lw - 1.001, w)
    y0 = np.floor(yi).astype(int); x0 = np.floor(xi).astype(int)
    fy = (yi - y0)[:, None]; fx = (xi - x0)[None, :]
    a = low[y0][:, x0]; b = low[y0][:, x0 + 1]; c = low[y0 + 1][:, x0]; d = low[y0 + 1][:, x0 + 1]
    return np.clip(np.round(a*(1-fy)*(1-fx)+b*(1-fy)*fx+c*fy*(1-fx)+d*fy*fx), 0, 255).astype(np.uint8)

REFY = smooth(101, W, H)
REFCB = smooth(202, W // 2, H // 2)
REFCR = smooth(303, W // 2, H // 2)
REFYf = REFY.astype(np.float64); REFCBf = REFCB.astype(np.float64); REFCRf = REFCR.astype(np.float64)
PR_RC, PR_CC = PR_R // 2, PR_C // 2   # chroma block origin


def build_host():
    rng = random.Random(55)
    noiseY = bytes(rng.randrange(0, 256) for _ in range(W * H))
    noiseC = bytes(rng.randrange(0, 256) for _ in range((W // 2) * (H // 2)))
    raw = (REFY.tobytes() + REFCB.tobytes() + REFCR.tobytes() +
           noiseY + noiseC + noiseC)
    open(f"{TMP}/host.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/host.yuv", "-c:v", "msmpeg4", "-qscale:v", "4",
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-vtag", "DIV3", f"{TMP}/host.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/host.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/host.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    pbytes = data[sizes[0]:sizes[0] + sizes[1]]
    avi = bytearray(open(f"{TMP}/host.avi", "rb").read())
    off = bytes(avi).find(pbytes)
    return avi, off, len(pbytes)

HOST, POFF, PLEN = build_host()


def hdr_bits(mv_idx=1, quant=4, rl=2, dc=0):
    c3 = {0: "0", 1: "10", 2: "11"}[rl]
    return "01" + format(quant, "05b") + "1" + c3 + str(dc) + str(mv_idx)


def make_pframe(mbtype_bits, suffix):
    bits = hdr_bits()
    for n in range(NMB):
        bits += ("0" + mbtype_bits + suffix) if n == PROBE_MB else "1"
    while len(bits) % 8:
        bits += "1"
    b = bytearray(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
    if len(b) < PLEN:
        b += bytes(PLEN - len(b))
    return bytes(b[:PLEN])


def decode(mbtype_bits, suffix):
    avi = bytearray(HOST)
    avi[POFF:POFF + PLEN] = make_pframe(mbtype_bits, suffix)
    open(f"{TMP}/t.avi", "wb").write(avi)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/t.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    if len(out) < 2 * fsz:
        return None
    base = fsz
    y = np.frombuffer(out[base:base + W*H], np.uint8).reshape(H, W).astype(np.float64)
    cb = np.frombuffer(out[base + W*H:base + W*H + (W//2)*(H//2)], np.uint8).reshape(H//2, W//2).astype(np.float64)
    cr = np.frombuffer(out[base + W*H + (W//2)*(H//2):base + fsz], np.uint8).reshape(H//2, W//2).astype(np.float64)
    return y, cb, cr


AR8 = np.arange(8); AR16 = np.arange(16)

def blk_sad(plane, ref, r0, c0, sz, ir, ic):
    rows = np.clip(r0 + ir + np.arange(sz), 0, ref.shape[0]-1)
    cols = np.clip(c0 + ic + np.arange(sz), 0, ref.shape[1]-1)
    return np.abs(ref[np.ix_(rows, cols)] - plane[r0:r0+sz, c0:c0+sz]).sum()


def classify(mbtype_bits, suffix):
    res = decode(mbtype_bits, suffix)
    if res is None:
        return None
    y, cb, cr = res
    # luma block origins (4 blocks)
    lb = [(PR_R, PR_C), (PR_R, PR_C+8), (PR_R+8, PR_C), (PR_R+8, PR_C+8)]
    # find best integer mv by minimizing 2nd-smallest luma block SAD (robust to coded blocks)
    best = None
    for iy in range(-14, 15):
        for ix in range(-14, 15):
            sads = sorted(blk_sad(y, REFYf, r, c, 8, iy, ix) for (r, c) in lb)
            cost = sads[1]   # 2nd smallest
            if best is None or cost < best[0]:
                best = (cost, ix, iy, sads)
    cost, ix, iy, _ = best
    INTRA_T = 600.0    # if even best shift leaves 2nd-block SAD large -> intra
    if cost > INTRA_T:
        # INTRA: cbp = blocks with AC energy (variance above flat-DC baseline)
        intra = 1
        coded = []
        for (r, c) in lb:
            blk = y[r:r+8, c:c+8]
            coded.append(1 if blk.var() > 12.0 else 0)
        for (cr0, cc0, ch) in [(PR_RC, PR_CC, cb), (PR_RC, PR_CC, cr)]:
            blk = ch[cr0:cr0+8, cc0:cc0+8]
            coded.append(1 if blk.var() > 12.0 else 0)
    else:
        intra = 0
        coded = []
        BLK_T = 200.0
        for (r, c) in lb:
            coded.append(1 if blk_sad(y, REFYf, r, c, 8, iy, ix) > BLK_T else 0)
        # chroma mv = mv/2 (floor toward zero like >>1)
        cix, ciy = ix >> 1, iy >> 1
        coded.append(1 if blk_sad(cb, REFCBf, PR_RC, PR_CC, 8, ciy, cix) > 120.0 else 0)
        coded.append(1 if blk_sad(cr, REFCRf, PR_RC, PR_CC, 8, ciy, cix) > 120.0 else 0)
    cbp = sum(coded[b] << (5 - b) for b in range(6))
    return (intra, cbp)


def load_existing():
    import re
    txt = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pframe_vlc.go")).read()
    m = re.search(r'var mbNonIntraVLC = .*?raw := map\[string\]\[2\]int\{(.*?)\}\s*return raw', txt, re.DOTALL)
    d = {}
    for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', m.group(1)):
        d[bits] = (int(a), int(b))
    return d


if __name__ == "__main__":
    import sys
    EX = load_existing()
    if sys.argv[1] == "calib":
        # classify known codewords with two suffixes; check stability + correctness
        good = bad = 0
        for code, (intra, cbp) in sorted(EX.items(), key=lambda kv: len(kv[0]))[:40]:
            a = classify(code, "0" * 24)
            b = classify(code, "1" * 24)
            mark = "ok" if (a == b == (intra, cbp)) else "BAD"
            if mark == "ok": good += 1
            else: bad += 1
            if mark == "BAD":
                print(f"  {code:>18} expect=({intra},{cbp:2d})  s0={a} s1={b}  {mark}")
        print(f"calib: good={good} bad={bad}")
