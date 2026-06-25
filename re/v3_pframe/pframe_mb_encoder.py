"""pframe_mb_encoder.py — black-box extraction of table_mb_non_intra (inter half).

Encoder-oracle, no ffmpeg source. Layout trick:
  frame0: top row + left column of MBs are FLAT (128); the rest is smooth texture.
  frame1: same flat border, texture shifted up-left by 2px so the interior moves.
Then the flat-border MBs encode as SKIP (1 bit each) and the first textured MB,
MB(1,1), is the first coded inter MB. Its predictor is 0 (all neighbours skip), so its
MV codeword is constant across clips. We force a specific cbp by perturbing exactly the
8x8 blocks whose cbp bit we want set; unperturbed blocks stay an exact motion match
(cbp bit clear).

  P = [hdr(12)] [skip * 7] [mb_type(inter,cbp)] [MV-code(const)] [residual blocks] ...

mb_type ends where the constant MV codeword begins (learned from the cbp=0 clip, whose
mb_type is the black-box-known "01"). So mb_type(cbp) = bits[19 : first index of MVconst].
"""
import os
import subprocess, os, random
import numpy as np

W, H = 112, 96
MBW, MBH = W // 16, H // 16            # 7 x 6
NMB = MBW * MBH
PROBE = MBW + 1                        # MB(row1,col1), first coded MB
HDR = 12
SKIPS = PROBE                          # MBs before probe are all skip
TMP = "/tmp/pf_mbe"
os.makedirs(TMP, exist_ok=True)


def smooth(seed):
    rng = random.Random(seed)
    lw, lh = W // 8 + 2, H // 8 + 2
    low = np.array([[rng.randrange(40, 216) for _ in range(lw)] for _ in range(lh)], np.float64)
    yi = np.linspace(0, lh - 1.001, H); xi = np.linspace(0, lw - 1.001, W)
    y0 = np.floor(yi).astype(int); x0 = np.floor(xi).astype(int)
    fy = (yi - y0)[:, None]; fx = (xi - x0)[None, :]
    a = low[y0][:, x0]; b = low[y0][:, x0+1]; c = low[y0+1][:, x0]; d = low[y0+1][:, x0+1]
    return np.clip(np.round(a*(1-fy)*(1-fx)+b*(1-fy)*fx+c*fy*(1-fx)+d*fy*fx), 0, 255).astype(np.float64)

TEX = smooth(7)
PR_R, PR_C = (PROBE // MBW) * 16, (PROBE % MBW) * 16


def apply_border(plane):
    plane = plane.copy()
    plane[:16, :] = 128
    plane[:, :16] = 128
    return plane


def make_frames(cbp):
    f0 = apply_border(TEX)
    # shift texture up-left by 2 px
    sh = np.empty_like(TEX)
    sh[:-2, :-2] = TEX[2:, 2:]
    sh[-2:, :] = TEX[-1:, :]; sh[:, -2:] = TEX[:, -1:]
    f1 = apply_border(sh)
    # perturb the 8x8 blocks of MB(1,1) whose cbp bit is set
    # block index: 0..3 luma (TL,TR,BL,BR), 4=Cb,5=Cr; cbp bit (5-blk)
    for blk in range(4):
        if (cbp >> (5 - blk)) & 1:
            br = PR_R + (blk // 2) * 8
            bc = PR_C + (blk % 2) * 8
            # mild additive residual: sets the cbp bit but keeps the (4,4) motion match
            f1[br:br + 8, bc:bc + 8] = np.clip(f1[br:br + 8, bc:bc + 8] + 18, 0, 255)
    return f0, f1


def encode_bits(cbp):
    f0, f1 = make_frames(cbp)
    cw, ch = W // 2, H // 2
    chroma0 = bytes([128]) * (cw * ch)
    # chroma: set Cb/Cr; for cbp chroma bits (blk4=Cb,5=Cr) perturb chroma block of MB(1,1)
    cb1 = np.full((ch, cw), 128.0); cr1 = np.full((ch, cw), 128.0)
    for blk, plane in ((4, cb1), (5, cr1)):
        if (cbp >> (5 - blk)) & 1:
            br, bc = PR_R // 2, PR_C // 2
            plane[br:br + 8, bc:bc + 8] = np.clip(plane[br:br + 8, bc:bc + 8] + 18, 0, 255)
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
    p = data[sizes[0]:sizes[0] + sizes[1]]
    return "".join(format(x, "08b") for x in p)


def load_existing():
    import re
    txt = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pframe_vlc.go")).read()
    m = re.search(r'var mbNonIntraVLC = .*?raw := map\[string\]\[2\]int\{(.*?)\}\s*return raw', txt, re.DOTALL)
    d = {}
    for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', m.group(1)):
        d[(int(a), int(b))] = bits
    return d


if __name__ == "__main__":
    import sys
    EX = load_existing()
    # learn the constant MV codeword and the skip-prefix from the cbp=0 clip
    b0 = encode_bits(0)
    # sanity: skip prefix should be 7 ones then mb_type
    print(f"cbp=0 bits[12:40] = {b0[HDR:HDR+28]}  (expect {SKIPS} skip ones then mb_type+MV)")
    after = b0[HDR + SKIPS + 1:]  # +1 = probe MB not-skip bit
    # mb_type(inter,cbp0) is the black-box-known "01"; the MV codeword follows.
    assert after.startswith("01"), after[:8]
    # Decode that MV codeword with our OWN black-box MV table to learn its exact bits.
    import json
    mv1 = {c: (v if v == "ESC" else tuple(v)) for c, v in json.load(open("/tmp/pf_oracle/mv1_blackbox.json")).items()}
    rest = after[2:]
    mvconst = None
    for L in range(1, 18):
        if rest[:L] in mv1:
            mvconst = rest[:L]; break
    print(f"learned MVconst = {mvconst} -> {mv1.get(mvconst)} (shift dmv expected (4,4))")
    if sys.argv[1:] and sys.argv[1] == "validate":
        # quick check a few cbp
        for cbp in [0, 8, 4, 32, 16, 63, 1, 2]:
            b = encode_bits(cbp)
            seg = b[HDR + SKIPS + 1:]
            idx = seg.find(mvconst, 0)
            mbt = seg[:idx]
            print(f"  cbp={cbp:2d} mb_type={mbt:>12} existing={EX.get((0,cbp))}  {'ok' if mbt==EX.get((0,cbp)) else 'BAD'}")
        sys.exit()
    # full sweep
    out = {}
    ok = bad = 0
    for cbp in range(64):
        b = encode_bits(cbp)
        seg = b[HDR + SKIPS + 1:]
        idx = seg.find(mvconst)
        if idx < 1:
            print(f"  cbp={cbp}: MVconst not found"); bad += 1; continue
        mbt = seg[:idx]
        out[mbt] = (0, cbp)
        ex = EX.get((0, cbp))
        if mbt == ex: ok += 1
        else:
            bad += 1
            print(f"  cbp={cbp:2d} mb_type={mbt:>14} existing={ex}  BAD")
    print(f"\ninter mb_type: ok={ok} bad={bad} / 64")
    import json
    json.dump({k: list(v) for k, v in out.items()}, open(f"{TMP}/mb_inter.json", "w"), indent=0)
