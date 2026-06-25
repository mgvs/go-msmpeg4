"""pframe_mb_extract.py — black-box extraction of table_mb_non_intra (inter half).

Encoder + decoder oracle, no ffmpeg source. We make one interior MB the first coded MB
(flat top-row/left-col border -> those MBs skip) and perturb chosen 8x8 blocks to vary
its cbp. For each clip we read back, FROM PIXELS, what the encoder actually did:
  - decode both frames with ffmpeg; ref = decoded I-frame, probe MB from decoded P-frame;
  - measure the MB motion vector (half-pel) by matching the probe MB to shifts of the ref;
  - per block, residual = decoded - MC(ref,mv); block coded (cbp bit set) iff residual>0.
Then mb_type is isolated from the bitstream: it ends exactly where the MV codeword begins,
and we know that codeword's bits from our OWN black-box MV table (mv -> code). So
  mb_type(code) -> (inter=0, cbp).   All recovered purely from observed encoder output.
"""
import os
import subprocess, os, json
import numpy as np

W, H = 112, 96
MBW, MBH = W // 16, H // 16
NMB = MBW * MBH
PROBE = MBW + 1
PR_R, PR_C = 16, 16
HDR, SKIPS = 12, PROBE
TMP = "/tmp/pf_mbx"
os.makedirs(TMP, exist_ok=True)
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def smooth(seed):
    import random
    rng = random.Random(seed)
    lw, lh = W // 8 + 2, H // 8 + 2
    low = np.array([[rng.randrange(40, 216) for _ in range(lw)] for _ in range(lh)], np.float64)
    yi = np.linspace(0, lh - 1.001, H); xi = np.linspace(0, lw - 1.001, W)
    y0 = np.floor(yi).astype(int); x0 = np.floor(xi).astype(int)
    fy = (yi - y0)[:, None]; fx = (xi - x0)[None, :]
    a = low[y0][:, x0]; b = low[y0][:, x0+1]; c = low[y0+1][:, x0]; d = low[y0+1][:, x0+1]
    return np.clip(np.round(a*(1-fy)*(1-fx)+b*(1-fy)*fx+c*fy*(1-fx)+d*fy*fx), 0, 255).astype(np.float64)

TEX = smooth(7)


def border(p):
    p = p.copy(); p[:16, :] = 128; p[:, :16] = 128; return p


def encode(perturb_luma, perturb_chroma, mag=30):
    f0 = border(TEX)
    sh = np.empty_like(TEX); sh[:-2, :-2] = TEX[2:, 2:]; sh[-2:, :] = TEX[-1:, :]; sh[:, -2:] = TEX[:, -1:]
    f1 = border(sh)
    ii, jj = np.indices((8, 8))
    checker = np.where((ii + jj) % 2 == 0, mag, -mag).astype(np.float64)  # zero-mean: keeps ME at (4,4)
    for blk in perturb_luma:
        br = PR_R + (blk // 2) * 8; bc = PR_C + (blk % 2) * 8
        f1[br:br+8, bc:bc+8] = np.clip(f1[br:br+8, bc:bc+8] + checker, 0, 255)
    cw, ch = W // 2, H // 2
    cb1 = np.full((ch, cw), 128.0); cr1 = np.full((ch, cw), 128.0)
    for blk in perturb_chroma:
        pl = cb1 if blk == 4 else cr1
        pl[8:16, 8:16] = np.clip(pl[8:16, 8:16] + checker, 0, 255)
    chroma0 = bytes([128]) * (cw * ch)
    raw = (f0.astype(np.uint8).tobytes() + chroma0 + chroma0 +
           f1.astype(np.uint8).tobytes() + cb1.astype(np.uint8).tobytes() + cr1.astype(np.uint8).tobytes())
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "msmpeg4", "-qscale:v", "4",
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-me_range", "32", "-mbd", "0", "-vtag", "DIV3", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    bits = "".join(format(x, "08b") for x in data[sizes[0]:sizes[0]+sizes[1]])
    # decode both frames
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    def planes(buf):
        y = np.frombuffer(buf[:W*H], np.uint8).reshape(H, W).astype(np.float64)
        cb = np.frombuffer(buf[W*H:W*H+(W//2)*(H//2)], np.uint8).reshape(H//2, W//2).astype(np.float64)
        cr = np.frombuffer(buf[W*H+(W//2)*(H//2):fsz], np.uint8).reshape(H//2, W//2).astype(np.float64)
        return y, cb, cr
    return bits, planes(out[:fsz]), planes(out[fsz:2*fsz])


def mc(ref, r0, c0, mx, my, sz):
    ix, fx = (mx - (mx % 2 + 2) % 2)//2, (mx % 2 + 2) % 2
    iy, fy = (my - (my % 2 + 2) % 2)//2, (my % 2 + 2) % 2
    Hh, Ww = ref.shape
    o = np.empty((sz, sz))
    for i in range(sz):
        for j in range(sz):
            sr = min(max(r0+i+iy, 0), Hh-1); sc = min(max(c0+j+ix, 0), Ww-1)
            a = ref[sr, sc]
            if fx == 0 and fy == 0: o[i, j] = a; continue
            b = ref[sr, min(max(c0+j+ix+1, 0), Ww-1)]
            c = ref[min(max(r0+i+iy+1, 0), Hh-1), sc]
            d = ref[min(max(r0+i+iy+1, 0), Hh-1), min(max(c0+j+ix+1, 0), Ww-1)]
            o[i, j] = (a+b+1)//2 if fy == 0 else (a+c+1)//2 if fx == 0 else (a+b+c+d+2)//4
    return o


def measure_mv(f1y, f0y):
    blk = f1y[PR_R:PR_R+16, PR_C:PR_C+16]
    best = None
    for iy in range(-12, 13):
        rows = np.clip(PR_R+iy+np.arange(16), 0, H-1)
        for ix in range(-12, 13):
            cols = np.clip(PR_C+ix+np.arange(16), 0, W-1)
            s = np.abs(f0y[np.ix_(rows, cols)] - blk).sum()
            if best is None or s < best[0]: best = (s, ix, iy)
    _, ix0, iy0 = best
    bh = None
    for my in (2*iy0-1, 2*iy0, 2*iy0+1):
        for mx in (2*ix0-1, 2*ix0, 2*ix0+1):
            e = np.abs(mc(f0y, PR_R, PR_C, mx, my, 16) - blk).sum()
            if bh is None or e < bh[0]: bh = (e, mx, my)
    return bh  # (err, mx, my)


def load_mv():
    mvs = {}
    for idx in (0, 1):
        d = json.load(open(f"/tmp/pf_oracle/mv{idx}_blackbox.json"))
        mvs[idx] = {(tuple(v) if v != "ESC" else "ESC"): c for c, v in d.items()}
    return mvs
MVCODE = load_mv()


def load_existing():
    import re
    txt = open(f"{PKG}/pframe_vlc.go").read()
    m = re.search(r'var mbNonIntraVLC = .*?raw := map\[string\]\[2\]int\{(.*?)\}\s*return raw', txt, re.DOTALL)
    d = {}
    for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', m.group(1)):
        d[bits] = (int(a), int(b))
    return d


def extract(perturb_luma, perturb_chroma, mag=30):
    bits, (f0y, f0cb, f0cr), (f1y, f1cb, f1cr) = encode(perturb_luma, perturb_chroma, mag)
    mvidx = int(bits[11])
    err, mx, my = measure_mv(f1y, f0y)
    # cbp from per-block residual after MC(ref,mv)
    coded = []
    lb = [(PR_R, PR_C), (PR_R, PR_C+8), (PR_R+8, PR_C), (PR_R+8, PR_C+8)]
    for (r, c) in lb:
        res = np.abs(mc(f0y, r, c, mx, my, 8) - f1y[r:r+8, c:c+8]).sum()
        coded.append(1 if res > 80 else 0)
    cmx, cmy = mx >> 1, my >> 1
    for (ref, plane) in ((f0cb, f1cb), (f0cr, f1cr)):
        res = np.abs(mc(ref, PR_R//2, PR_C//2, cmx, cmy, 8) - plane[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8]).sum()
        coded.append(1 if res > 60 else 0)
    cbp = sum(coded[b] << (5 - b) for b in range(6))
    # isolate mb_type via the (known) MV codeword that follows it
    mvcode = MVCODE[mvidx].get((mx, my))
    if mvcode is None:
        return None
    seg = bits[HDR + SKIPS + 1:]   # after header + skip MBs + probe not-skip bit
    idx = seg.find(mvcode)
    if idx < 1 or idx > 20:
        return None
    return seg[:idx], (0, cbp), mvidx, (mx, my)


if __name__ == "__main__":
    EX = load_existing()
    found = {}       # cbp -> mbtype_code
    # targeted: perturb the luma/chroma blocks of each cbp pattern
    for cbp in range(64):
        pl = [b for b in range(4) if (cbp >> (5 - b)) & 1]
        pc = [b for b in (4, 5) if (cbp >> (5 - b)) & 1]
        for mag in (10, 16, 24, 30):
            r = extract(pl, pc, mag)
            if r is None:
                continue
            code, (intra, acbp), mvidx, mv = r
            if acbp not in found:
                found[acbp] = code
            if acbp == cbp:
                break
    miss = [c for c in range(64) if c not in found]
    print(f"after targeted pass: {len(found)}/64 inter cbp; missing={miss}")
    # verify
    ok = bad = 0
    for cbp, code in sorted(found.items()):
        ex = EX.get(code)
        if ex == (0, cbp): ok += 1
        else:
            bad += 1
            print(f"  cbp={cbp:2d} code={code} -> existing says {ex}")
    print(f"verify vs existing: ok={ok} bad={bad}")
    json.dump({code: [0, cbp] for cbp, code in found.items()}, open(f"{TMP}/mb_inter.json", "w"))
