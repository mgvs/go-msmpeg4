"""pframe_oracle.py — decoder-oracle for the MS-MPEG4 v3 motion-vector VLC.

Fully black-box: uses ONLY the ffmpeg DECODER (binary) as a pixel oracle plus the
black-box-recovered constants (P-frame header layout, skip bit, and the
mb_type(inter,cbp0) codeword "01", recovered structurally in pframe_mv_extract.py).
No ffmpeg source and no VLC table are ever read.

Idea: we hand-build a P-frame in which exactly one *interior* macroblock is coded as
inter/cbp0 carrying a candidate MV codeword; all other MBs are skipped. We patch those
bits into a real 2-frame DIV3 AVI (I=ref, P=slot), decode with ffmpeg, and measure the
chosen MB's displacement against the reference frame -> that is (dmvx,dmvy) for the code.

Walking the binary prefix tree (a candidate p is a complete codeword iff the decoded MV
is invariant to the bits that follow p) recovers every leaf of the complete VLC.
"""
import os
import subprocess, os, random
import numpy as np

W, H = 96, 80
MBW, MBH = W // 16, H // 16          # 6 x 5
NMB = MBW * MBH
PROBE_MB = (MBH // 2) * MBW + (MBW // 2)   # an interior MB index (raster)
TMP = "/tmp/pf_oracle"
os.makedirs(TMP, exist_ok=True)

# ---- reference frame: smooth, locally unique (good for unambiguous MV matching) ----
rng = random.Random(777)
low = np.array([[rng.randrange(40, 216) for _ in range(W // 8 + 2)] for _ in range(H // 8 + 2)], np.float64)
def upscale(low, w, h):
    yi = np.linspace(0, low.shape[0] - 1.001, h)
    xi = np.linspace(0, low.shape[1] - 1.001, w)
    y0 = np.floor(yi).astype(int); x0 = np.floor(xi).astype(int)
    fy = (yi - y0)[:, None]; fx = (xi - x0)[None, :]
    a = low[y0][:, x0]; b = low[y0][:, x0 + 1]; c = low[y0 + 1][:, x0]; d = low[y0 + 1][:, x0 + 1]
    return a * (1 - fy) * (1 - fx) + b * (1 - fy) * fx + c * fy * (1 - fx) + d * fy * fx
REF = np.clip(np.round(upscale(low, W, H)), 0, 255).astype(np.uint8)
CHROMA = bytes([128]) * ((W // 2) * (H // 2))


def build_host():
    """Encode [I=REF, P=noise] so we have a P-packet slot with plenty of bytes."""
    rng2 = random.Random(999)
    noise = bytes(rng2.randrange(0, 256) for _ in range(W * H))
    raw = REF.tobytes() + CHROMA + CHROMA + noise + CHROMA + CHROMA
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
    assert off >= 0
    return avi, off, len(pbytes)

HOST, POFF, PLEN = build_host()


def hdr_bits(mv_idx, quant=4, rl=2, dc=1):
    # pictype=01, quant u5, use_skip=1, rl c3 (0->'0',1->'10',2->'11'), dc u1, mv u1
    c3 = {0: "0", 1: "10", 2: "11"}[rl]
    return "01" + format(quant, "05b") + "1" + c3 + str(dc) + str(mv_idx)


def make_pframe(mv_bits, mv_idx):
    bits = hdr_bits(mv_idx)
    for n in range(NMB):
        if n == PROBE_MB:
            bits += "0" + "01" + mv_bits        # not-skip + mb_type(inter,cbp0) + MV code
        else:
            bits += "1"                          # skip
    while len(bits) % 8:
        bits += "1"
    b = bytearray(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    if len(b) < PLEN:
        b += bytes(PLEN - len(b))               # zero-pad; decoder stops after NMB MBs
    return bytes(b[:PLEN])


def decode_frame1(mv_bits, mv_idx):
    avi = bytearray(HOST)
    avi[POFF:POFF + PLEN] = make_pframe(mv_bits, mv_idx)
    open(f"{TMP}/t.avi", "wb").write(avi)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/t.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    if len(out) < 2 * fsz:
        return None
    y1 = np.frombuffer(out[fsz:fsz + W * H], np.uint8).reshape(H, W).astype(np.float64)
    return y1


def mc_block(ref, r0, c0, mx, my):
    """Reproduce pframe.go mcFill: half-pel bilinear with edge clamp, 16x16 luma block."""
    ix, fx = (mx - (mx % 2 + 2) % 2) // 2, (mx % 2 + 2) % 2
    iy, fy = (my - (my % 2 + 2) % 2) // 2, (my % 2 + 2) % 2
    out = np.empty((16, 16))
    for i in range(16):
        for j in range(16):
            sr = min(max(r0 + i + iy, 0), H - 1); sc = min(max(c0 + j + ix, 0), W - 1)
            a = ref[sr, sc]
            if fx == 0 and fy == 0:
                out[i, j] = a; continue
            b = ref[sr, min(max(c0 + j + ix + 1, 0), W - 1)]
            c = ref[min(max(r0 + i + iy + 1, 0), H - 1), sc]
            d = ref[min(max(r0 + i + iy + 1, 0), H - 1), min(max(c0 + j + ix + 1, 0), W - 1)]
            if fx == 1 and fy == 0: out[i, j] = (a + b + 1) // 2
            elif fx == 0 and fy == 1: out[i, j] = (a + c + 1) // 2
            else: out[i, j] = (a + b + c + d + 2) // 4
    return out


PR_R = (PROBE_MB // MBW) * 16
PR_C = (PROBE_MB % MBW) * 16
REFF = REF.astype(np.float64)
AR = np.arange(16)


def measure_mv(y1):
    """Fast: integer SAD pre-search then half-pel refine. Returns (mx,my) in half-pel."""
    blk = y1[PR_R:PR_R + 16, PR_C:PR_C + 16]
    best = None
    for iy in range(-18, 19):
        rows = np.clip(PR_R + iy + AR, 0, H - 1)
        for ix in range(-18, 19):
            cols = np.clip(PR_C + ix + AR, 0, W - 1)
            sad = np.abs(REFF[np.ix_(rows, cols)] - blk).sum()
            if best is None or sad < best[0]:
                best = (sad, ix, iy)
    _, ix0, iy0 = best
    besth = None
    for my in (2 * iy0 - 1, 2 * iy0, 2 * iy0 + 1):
        for mx in (2 * ix0 - 1, 2 * ix0, 2 * ix0 + 1):
            err = np.abs(mc_block(REFF, PR_R, PR_C, mx, my) - blk).sum()
            if besth is None or err < besth[0]:
                besth = (err, mx, my)
    return (besth[1], besth[2])


def decode_mv(mv_bits, mv_idx):
    y1 = decode_frame1(mv_bits, mv_idx)
    if y1 is None:
        return None
    return measure_mv(y1)


# ---- escape detection: probed MV reads two literal u(6) fields after the codeword ----
ESC_R = ["101000011000", "011000101000"]   # -> (8,-8) and (-8,8)
ESC_V = [(8, -8), (-8, 8)]


def is_escape(p, idx):
    for R, V in zip(ESC_R, ESC_V):
        if decode_mv(p + R + "1" * 8, idx) != V:
            return False
    return True


def walk_tree(idx, maxlen=18, log_every=64):
    """DFS the MV prefix tree. Returns {codeword(str): (dmvx,dmvy) or 'ESC'}."""
    leaves = {}
    stack = [""]
    visited = 0
    while stack:
        p = stack.pop()
        if len(p) > maxlen:
            leaves[p] = "ESC?"  # safety; should be caught earlier
            continue
        vA = decode_mv(p + "0" + "1" * (maxlen - len(p)), idx)
        vB = decode_mv(p + "1" + "1" * (maxlen - len(p)), idx)
        visited += 1
        if visited % log_every == 0:
            print(f"  [idx{idx}] visited={visited} leaves={len(leaves)} stack={len(stack)} depth={len(p)}", flush=True)
        if vA is not None and vA == vB:
            leaves[p] = vA            # complete codeword == p
            continue
        if len(p) >= 4 and is_escape(p, idx):
            leaves[p] = "ESC"
            continue
        stack.append(p + "1")
        stack.append(p + "0")
    return leaves


def load_existing(idx):
    import re
    txt = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pframe_mv_vlc.go")).read()
    m = re.search(rf'var mvVLC{idx} = .*?raw := map\[string\]\[2\]int\{{(.*?)\}}\s*return raw', txt, re.DOTALL)
    tbl = {}
    for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', m.group(1)):
        tbl[bits] = (int(a), int(b))
    return tbl


if __name__ == "__main__":
    import sys, json, time
    if sys.argv[1] == "validate":
        tbl = load_existing(1)
        samples = ["00", "1001", "1111", "0111", "10001", "010001", "0101101", "011011000", "010011110", "1011"]
        print(f"PROBE_MB={PROBE_MB} at (row={PR_R},col={PR_C}); PLEN={PLEN}")
        for code in samples:
            print(f"  code={code:>10} expect={tbl.get(code)} measured={decode_mv(code, 1)} esc={is_escape(code,1) if len(code)>=4 else '-'}")
        sys.exit()
    idx = int(sys.argv[1])
    t0 = time.time()
    leaves = walk_tree(idx)
    dt = time.time() - t0
    out = {}
    for code, v in leaves.items():
        out[code] = "ESC" if v == "ESC" else list(v)
    json.dump(out, open(f"{TMP}/mv{idx}_blackbox.json", "w"), indent=0)
    print(f"\nidx{idx}: {len(leaves)} leaves in {dt:.0f}s -> {TMP}/mv{idx}_blackbox.json")
    # compare to existing (sanity, not part of derivation)
    exist = load_existing(idx)
    # existing escape marker is (-32,-32)
    ok = bad = onlyx = onlye = 0
    diffs = []
    for code, v in out.items():
        ev = exist.get(code)
        bv = "ESC" if v == "ESC" else tuple(v)
        eev = "ESC" if ev == (-32, -32) else ev
        if ev is None:
            onlyx += 1
        elif bv == eev:
            ok += 1
        else:
            bad += 1
            if len(diffs) < 40:
                diffs.append((code, bv, eev))
    for code in exist:
        if code not in out:
            onlye += 1
    print(f"compare vs existing mvVLC{idx}: match={ok} mismatch={bad} only-in-blackbox={onlyx} only-in-existing={onlye}")
    for c, b, e in diffs:
        print(f"  {c}: blackbox={b} existing={e}")
