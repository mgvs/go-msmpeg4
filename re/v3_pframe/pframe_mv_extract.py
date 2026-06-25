"""pframe_mv_extract.py — TABLE-FREE black-box extraction of MV codewords.

Method (no VLC table read at any point):
  Encode a 2-frame clip [I = noise, P = noise shifted by (dx,dy)] with ffmpeg.
  In the P-frame, only MB(0,0) carries a real MV (predictor 0 -> dmv = MV);
  every later MB has dmv=0 and encodes as the constant 5-bit unit U="00100"
  (skip0 + mb_type(inter,cbp0)="01" + dmv0-code="00").

  P = [hdr(12)] [skip0] [mb_type] [MVcode(MB0)] [U * (N-1)] [byte pad]

  - mb_type(inter,cbp0) is recovered as the longest common prefix of bits[13:]
    across many clips (needs MV codes that start with both 0 and 1).
  - MVcode(MB0) ends where the maximal U-repeat tail begins.
  - The decoded MV equals (-2dx, -2dy) in half-pel units (motion points to source).

We then cross-check the recovered (dmv -> code) against our existing mvVLC1
(comparison only; the extraction itself never consulted a table).
"""
import os
import subprocess, os, random, re, sys
import numpy as np

W, H = 96, 80
NMB = (W // 16) * (H // 16)
U = "00100"            # constant per-MB unit for dmv=0 inter-cbp0
TMP = "/tmp/pf_mv"
os.makedirs(TMP, exist_ok=True)
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

random.seed(1234)
Y0 = np.array([random.randrange(16, 240) for _ in range(W * H)], dtype=np.float64).reshape(H, W)
CHROMA = bytes([128]) * ((W // 2) * (H // 2))


def shifted_plane(dx2, dy2):
    """Shift by half-pel amounts dx2,dy2 (in half-pixels). Bilinear; clamp edges."""
    dx = dx2 / 2.0
    dy = dy2 / 2.0
    rr = np.arange(H)[:, None] - dy
    cc = np.arange(W)[None, :] - dx
    r0 = np.clip(np.floor(rr).astype(int), 0, H - 1)
    c0 = np.clip(np.floor(cc).astype(int), 0, W - 1)
    r1 = np.clip(r0 + 1, 0, H - 1)
    c1 = np.clip(c0 + 1, 0, W - 1)
    fr = np.clip(rr - np.floor(rr), 0, 1)
    fc = np.clip(cc - np.floor(cc), 0, 1)
    out = (Y0[r0, c0] * (1 - fr) * (1 - fc) + Y0[r0, c1] * (1 - fr) * fc +
           Y0[r1, c0] * fr * (1 - fc) + Y0[r1, c1] * fr * fc)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def pframe_bits(dx2, dy2):
    y1 = shifted_plane(dx2, dy2).tobytes()
    raw = Y0.astype(np.uint8).tobytes() + CHROMA + CHROMA + y1 + CHROMA + CHROMA
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "msmpeg4", "-qscale:v", "4",
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-me_range", "64", "-vtag", "DIV3", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    p = data[sizes[0]:sizes[0] + sizes[1]]
    return "".join(format(x, "08b") for x in p)


def tail_start(bs):
    """Index where the maximal trailing run of repeated U (allowing byte-pad after) begins.
    We require at least 10 consecutive U units to be unambiguous."""
    best = None
    # candidate region for MB1.. : scan possible starts, pick the earliest T from which
    # bits follow U*k for the largest k.
    for T in range(13, len(bs)):
        k = 0
        j = T
        while j + 5 <= len(bs) and bs[j:j + 5] == U:
            k += 1
            j += 5
        if k >= 10:
            return T, k
    return None, 0


def extract(dx2, dy2):
    bs = pframe_bits(dx2, dy2)
    # header sanity
    assert bs[:2] == "01", bs[:2]
    T, k = tail_start(bs)
    if T is None:
        return None  # not a clean global-shift frame (residual/intra contamination)
    body = bs[13:T]        # mb_type + MVcode
    return bs, body, T, k


# ---- existing table for cross-check only ----
def load_map(path, var):
    txt = open(path).read()
    m = re.search(rf'var {var} = func\(\) map\[string\]\[2\]int \{{.*?raw := map\[string\]\[2\]int\{{(.*?)\}}\s*return raw', txt, re.DOTALL)
    d = {}
    for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', m.group(1)):
        d[bits] = (int(a), int(b))
    return d

MV1 = load_map(f"{PKG}/pframe_mv_vlc.go", "mvVLC1")
MV1_by_val = {v: kbits for kbits, v in MV1.items()}


if __name__ == "__main__":
    # integer-pixel sweep first (dmv even); add half-pel (odd) too.
    targets = []
    for ix in range(-7, 8):
        for iy in range(-7, 8):
            targets.append((2 * ix, 2 * iy))   # integer px -> dmv=(-2dx,-2dy) even
    bodies = {}
    for (dx2, dy2) in targets:
        res = extract(dx2, dy2)
        if res is None:
            continue
        bs, body, T, k = res
        # reject contaminated clips: a clean MB0 = mb_type(2) + MVcode(<=17) <= ~20 bits.
        if len(body) > 20:
            continue
        # expected dmv (decoder convention): motion points to source => -shift
        exp = (-dx2, -dy2)
        bodies[exp] = body
    # recover mb_type = longest common prefix of all bodies
    blist = list(bodies.values())
    if not blist:
        print("no clean clips"); sys.exit()
    lcp = blist[0]
    for b in blist[1:]:
        i = 0
        while i < min(len(lcp), len(b)) and lcp[i] == b[i]:
            i += 1
        lcp = lcp[:i]
    print(f"recovered mb_type(inter,cbp0) prefix = '{lcp}' (len {len(lcp)})  [existing table: '01']")
    mbt = lcp
    ok = bad = miss = 0
    fails = []
    for exp, body in sorted(bodies.items()):
        mvcode = body[len(mbt):]
        ref = MV1_by_val.get(exp)
        if ref is None:
            miss += 1
            continue
        if mvcode == ref:
            ok += 1
        else:
            bad += 1
            fails.append((exp, mvcode, ref))
    print(f"clean clips: {len(bodies)} | MV match existing: {ok} | MISMATCH: {bad} | not-in-table: {miss}")
    for exp, got, ref in fails[:30]:
        print(f"  dmv={exp}: extracted={got}  existing={ref}")
