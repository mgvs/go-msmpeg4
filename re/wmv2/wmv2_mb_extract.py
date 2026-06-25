"""wmv2_mb_extract.py — black-box extraction of the 3 WMV2 P-frame mb_non_intra VLC tables
(ff_wmv2_inter_table[0/1/2]), inter half. Encoder+decoder oracle, no ffmpeg source.

Same idea as the v3 pframe_mb_extract.py: flat border MBs skip, one interior probe MB is the
first coded MB; a uniform integer shift gives it motion (even MV -> no mspel/hshift); a zero-mean
checker perturbs chosen 8x8 blocks to set its cbp. We read back from pixels the MB's MV (half-pel)
and which blocks are coded; mb_type is isolated as the bits before the (known, black-box) MV
codeword. The WMV2 P-header is parsed to find the MB-layer start and cbp_index, which (with qscale)
selects WHICH of the 3 tables this frame exercises: cbp_table_index = map[(q>10)+(q>20)][cbp_index].

ffmpeg's wmv2 encoder fixes extradata flags mspel=loop=abt=jtype=per_mb_rl=1, top_left_mv=0, so
wmv2_pred_motion never reads a bit (top_left_mv_flag=0); the anchor mb_type->MV is clean when the
frame uses per_mb_rl_table=0 and abt_type=0 (verified from the parsed header).
"""
import subprocess, os, json, re
import numpy as np

W, H = 112, 96
MBW, MBH = W // 16, H // 16
NMB = MBW * MBH
PROBE = 0
PR_R, PR_C = 0, 0
TMP = "/tmp/wmv2_mbx"
os.makedirs(TMP, exist_ok=True)
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CBP_MAP = [[0, 2, 1], [1, 0, 2], [2, 1, 0]]  # wmv2_get_cbp_table_index


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


def encode(perturb_luma, perturb_chroma, q, mag=30):
    # Only the probe MB differs from the reference -> every other MB is a perfect SKIP, so the
    # probe is the single coded MB (its mb_type is at the MB-layer start). The probe MB is a 2px
    # (even MV, no mspel) shifted copy of the reference there, plus a zero-mean checker on chosen
    # blocks to set cbp.
    f0 = TEX.copy()
    f1 = TEX.copy()
    f1[PR_R:PR_R+16, PR_C:PR_C+16] = TEX[PR_R+2:PR_R+18, PR_C+2:PR_C+18]  # MV=(4,4) half-pel
    ii, jj = np.indices((8, 8))
    checker = np.where((ii + jj) % 2 == 0, mag, -mag).astype(np.float64)
    for blk in perturb_luma:
        br = PR_R + (blk // 2) * 8; bc = PR_C + (blk % 2) * 8
        f1[br:br+8, bc:bc+8] = np.clip(f1[br:br+8, bc:bc+8] + checker, 0, 255)
    cw, ch = W // 2, H // 2
    cb0 = smooth(202)[:ch, :cw]; cr0 = smooth(303)[:ch, :cw]
    cb1 = cb0.copy(); cr1 = cr0.copy()
    pr, pc = PR_R // 2, PR_C // 2
    cb1[pr:pr+8, pc:pc+8] = cb0[pr+1:pr+9, pc+1:pc+9]  # chroma MV = (mv>>1)|(mv&1) = 2 -> 1px
    cr1[pr:pr+8, pc:pc+8] = cr0[pr+1:pr+9, pc+1:pc+9]
    for blk in perturb_chroma:
        pl0, pl1 = (cb0, cb1) if blk == 4 else (cr0, cr1)
        pl1[pr:pr+8, pc:pc+8] = np.clip(pl1[pr:pr+8, pc:pc+8] + checker, 0, 255)
    raw = (f0.astype(np.uint8).tobytes() + cb0.astype(np.uint8).tobytes() + cr0.astype(np.uint8).tobytes() +
           f1.astype(np.uint8).tobytes() + cb1.astype(np.uint8).tobytes() + cr1.astype(np.uint8).tobytes())
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "wmv2", "-qscale:v", str(q),
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-me_range", "32", "-mbd", "0", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    bits = "".join(format(x, "08b") for x in data[sizes[0]:sizes[0]+sizes[1]])
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
    ix, fx = (mx - ((mx % 2 + 2) % 2)) // 2, (mx % 2 + 2) % 2
    iy, fy = (my - ((my % 2 + 2) % 2)) // 2, (my % 2 + 2) % 2
    Hh, Ww = ref.shape; o = np.empty((sz, sz))
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
    blk = f1y[PR_R:PR_R+16, PR_C:PR_C+16]; best = None
    for iy in range(-12, 13):
        rows = np.clip(PR_R+iy+np.arange(16), 0, H-1)
        for ix in range(-12, 13):
            cols = np.clip(PR_C+ix+np.arange(16), 0, W-1)
            s = np.abs(f0y[np.ix_(rows, cols)] - blk).sum()
            if best is None or s < best[0]: best = (s, ix, iy)
    _, ix0, iy0 = best; bh = None
    for my in (2*iy0-1, 2*iy0, 2*iy0+1):
        for mx in (2*ix0-1, 2*ix0, 2*ix0+1):
            e = np.abs(mc(f0y, PR_R, PR_C, mx, my, 16) - blk).sum()
            if bh is None or e < bh[0]: bh = (e, mx, my)
    return bh[1], bh[2]


def load_mv():
    txt = open(f"{PKG}/pframe_mv_vlc.go").read()
    mvs = {}
    for idx, var in ((0, "mvVLC0"), (1, "mvVLC1")):
        m = re.search(rf'var {var} = .*?raw := map\[string\]\[2\]int\{{(.*?)\}}\s*return raw', txt, re.DOTALL)
        d = {}
        for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', m.group(1)):
            d[(int(a), int(b))] = bits
        mvs[idx] = d
    return mvs
MVCODE = load_mv()


def decode012(bits, i):
    if bits[i] == "0":
        return 0, i + 1
    return (1 if bits[i+1] == "0" else 2), i + 2


def parse_header(bits):
    """Return (mb_layer_start, cbp_table_index, ok) — parses the WMV2 P-header up to MB layer.
    extradata flags: mspel_bit=abt_flag=per_mb_rl_bit=1, top_left_mv_flag=0 (ffmpeg encoder)."""
    i = 0
    if bits[i] != "1":
        return None  # not a P-frame
    i += 1
    q = int(bits[i:i+5], 2); i += 5
    st = bits[i:i+2]; i += 2
    if st == "00":      # NONE
        pass
    elif st == "01":    # MPEG: 1 bit per MB
        i += NMB
    elif st == "10":    # ROW: per row, bit; if 0 -> MBW bits
        for _ in range(MBH):
            b = bits[i]; i += 1
            if b == "0": i += MBW
    else:               # COL: per col, bit; if 0 -> MBH bits
        for _ in range(MBW):
            b = bits[i]; i += 1
            if b == "0": i += MBH
    cbp_index, i = decode012(bits, i)
    mspel = bits[i]; i += 1
    per_mb_abt = (bits[i] == "0"); i += 1   # per_mb_abt = bit^1 -> per_mb_abt true when bit==0
    abt_type = 0
    if not per_mb_abt:
        abt_type, i = decode012(bits, i)
    per_mb_rl = bits[i]; i += 1
    if per_mb_rl == "0":
        _, i = decode012(bits, i)  # rl
    dc = bits[i]; i += 1
    mv_idx = int(bits[i]); i += 1
    cbp_ti = CBP_MAP[(q > 10) + (q > 20)][cbp_index]
    clean = (st in ("01", "00")) and per_mb_rl == "0" and abt_type == 0 and not per_mb_abt
    return i, cbp_ti, mv_idx, clean, st


def extract(perturb_luma, perturb_chroma, q, mag=30):
    bits, (f0y, f0cb, f0cr), (f1y, f1cb, f1cr) = encode(perturb_luma, perturb_chroma, q, mag)
    ph = parse_header(bits)
    if ph is None:
        return None
    start, table, mv_idx, clean, st = ph
    if not clean:
        return None  # only use frames with a clean mb_type->MV anchor
    seg = bits[start:]
    # the probe MB is a known 2px shift -> MV should be (4,4); prefer the measured MV (accurate for
    # light cbp) and fall back to the known (4,4) when measurement is thrown off by heavy residual.
    candidates = [measure_mv(f1y, f0y), (4, 4)]
    for (mx, my) in candidates:
        mvcode = MVCODE[mv_idx].get((mx, my))
        if mvcode is None:
            continue
        idx = seg.find(mvcode)
        if idx < 1 or idx > 22:
            continue
        coded = []
        lb = [(PR_R, PR_C), (PR_R, PR_C+8), (PR_R+8, PR_C), (PR_R+8, PR_C+8)]
        for (r, c) in lb:
            res = np.abs(mc(f0y, r, c, mx, my, 8) - f1y[r:r+8, c:c+8]).sum()
            coded.append(1 if res > 80 else 0)
        cmx, cmy = (mx >> 1) | (mx & 1), (my >> 1) | (my & 1)
        for (ref, plane) in ((f0cb, f1cb), (f0cr, f1cr)):
            res = np.abs(mc(ref, PR_R//2, PR_C//2, cmx, cmy, 8) - plane[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8]).sum()
            coded.append(1 if res > 60 else 0)
        cbp = sum(coded[b] << (5 - b) for b in range(6))
        return table, seg[:idx], cbp
    return None


if __name__ == "__main__":
    import sys
    from collections import Counter
    # collect MANY (table,cbp)->code observations, then majority-vote to filter noise.
    obs = {0: {c: Counter() for c in range(64)}, 1: {c: Counter() for c in range(64)}, 2: {c: Counter() for c in range(64)}}
    for q in [2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 28, 31]:
        for cbp in range(64):
            pl = [b for b in range(4) if (cbp >> (5 - b)) & 1]
            pc = [b for b in (4, 5) if (cbp >> (5 - b)) & 1]
            for mag in (12, 18, 24, 30, 40):
                r = extract(pl, pc, q, mag)
                if r is None:
                    continue
                table, code, acbp = r
                obs[table][acbp][code] += 1
    json.dump({t: {c: dict(obs[t][c]) for c in range(64) if obs[t][c]} for t in (0, 1, 2)},
              open(f"{TMP}/mb_inter_votes.json", "w"))
    for t in (0, 1, 2):
        # resolve by vote, then break prefix conflicts by dropping the lower-voted side
        cand = {c: obs[t][c].most_common(1)[0][0] for c in range(64) if obs[t][c]}
        votes = {c: obs[t][c][cand[c]] for c in cand}
        changed = True
        while changed:
            changed = False
            items = list(cand.items())
            for ci, a in items:
                for cj, b in items:
                    if ci != cj and a != b and (b.startswith(a) or a.startswith(b)):
                        loser = ci if votes[ci] <= votes[cj] else cj
                        # try the next-best code for the loser
                        alts = obs[t][loser].most_common()
                        nxt = next((code for code, _ in alts if code != cand[loser]), None)
                        if nxt:
                            cand[loser] = nxt; votes[loser] = obs[t][loser][nxt]
                        else:
                            del cand[loser]; del votes[loser]
                        changed = True
                        break
                if changed:
                    break
        got = {c: cand[c] for c in cand}
        miss = [c for c in range(64) if c not in got]
        codes = list(got.values())
        prefix_free = all(not (a != b and b.startswith(a)) for a in codes for b in codes)
        bij = len(set(codes)) == len(codes)
        print(f"table {t}: inter {len(got)}/64 missing={miss} prefix_free={prefix_free} bijective={bij}")
        json.dump({code: [0, cbp] for cbp, code in got.items()}, open(f"{TMP}/mb_inter_t{t}.json", "w"))
