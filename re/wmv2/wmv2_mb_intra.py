"""wmv2_mb_intra.py — black-box extraction of the intra half of the 3 WMV2 P-frame mb_non_intra
VLC tables. Encoder-oracle first-diff (like v3 pframe_mb_intra.py), adapted for WMV2.

Probe = MB0. Its reference region is random (kills inter prediction -> the encoder codes it INTRA);
its content is block0 flat at blk0_base (+ zero-mean AC if coded) and other coded blocks random.
Two clips differ ONLY in blk0_base, so their bitstreams diverge exactly at block0's DC codeword,
which sits right after mb_type + ac_pred(1). The first-diff bounds mb_type (1-bit ac_pred ambiguity
resolved by requiring the codeword to be prefix-free vs the inter codes of the same table).
parse_header gives the MB-layer start and the cbp_table_index for the frame.
"""
import subprocess, os, json, random
import numpy as np
import wmv2_mb_extract as E  # reuse parse_header, smooth, W/H, TMP, etc.

W, H = E.W, E.H
PR_R, PR_C = 0, 0
TMP = "/tmp/wmv2_mbi"
os.makedirs(TMP, exist_ok=True)


def encode_intra(coded_luma, coded_chroma, blk0_base, q):
    rng = random.Random(4242)
    f0 = E.TEX.copy()
    rng0 = random.Random(0x5a5a)
    for i in range(16):
        for j in range(16):
            f0[PR_R + i, PR_C + j] = rng0.randrange(20, 236)  # random ref -> no inter match
    f1 = E.TEX.copy()
    f1[PR_R:PR_R+16, PR_C:PR_C+16] = 128.0
    b0coded = 0 in coded_luma
    for i in range(8):
        for j in range(8):
            v = blk0_base + ((20 if (i + j) % 2 else -20) if b0coded else 0)
            f1[PR_R + i, PR_C + j] = min(max(v, 0), 255)
    for blk in coded_luma:
        if blk == 0:
            continue
        br = PR_R + (blk // 2) * 8; bc = PR_C + (blk % 2) * 8
        for i in range(8):
            for j in range(8):
                f1[br + i, bc + j] = rng.randrange(20, 236)
    cw, ch = W // 2, H // 2
    cb0 = E.smooth(202)[:ch, :cw].copy(); cr0 = E.smooth(303)[:ch, :cw].copy()
    cb1 = cb0.copy(); cr1 = cr0.copy()
    cb1[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8] = 128.0
    cr1[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8] = 128.0
    for blk in coded_chroma:
        pl = cb1 if blk == 4 else cr1
        for i in range(8):
            for j in range(8):
                pl[PR_R//2 + i, PR_C//2 + j] = rng.randrange(20, 236)
    raw = (f0.astype(np.uint8).tobytes() + cb0.astype(np.uint8).tobytes() + cr0.astype(np.uint8).tobytes() +
           f1.astype(np.uint8).tobytes() + cb1.astype(np.uint8).tobytes() + cr1.astype(np.uint8).tobytes())
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "wmv2", "-qscale:v", str(q),
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-mbd", "0", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    bits = "".join(format(x, "08b") for x in data[sizes[0]:sizes[0]+sizes[1]])
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    p = out[fsz:2*fsz]
    y = np.frombuffer(p[:W*H], np.uint8).reshape(H, W).astype(np.float64)
    cb = np.frombuffer(p[W*H:W*H+(W//2)*(H//2)], np.uint8).reshape(H//2, W//2).astype(np.float64)
    cr = np.frombuffer(p[W*H+(W//2)*(H//2):fsz], np.uint8).reshape(H//2, W//2).astype(np.float64)
    return bits, y, cb, cr


def read_cbp(y, cb, cr):
    coded = []
    for blk in range(4):
        br = PR_R + (blk // 2) * 8; bc = PR_C + (blk % 2) * 8
        coded.append(1 if y[br:br+8, bc:bc+8].var() > 8 else 0)
    coded.append(1 if cb[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8].var() > 8 else 0)
    coded.append(1 if cr[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8].var() > 8 else 0)
    return sum(coded[b] << (5 - b) for b in range(6))


def load_inter():
    inter = {}
    for t in (0, 1, 2):
        try:
            d = json.load(open(f"/tmp/wmv2_mbx/mb_inter_t{t}.json"))
            inter[t] = set(d.keys())
        except FileNotFoundError:
            inter[t] = set()
    return inter
INTER = load_inter()


def extract_intra(cbp, q, known):
    cl = [b for b in range(4) if (cbp >> (5 - b)) & 1]
    cc = [b for b in (4, 5) if (cbp >> (5 - b)) & 1]
    out = []
    for ba, bb in [(128, 40), (128, 210), (70, 200), (100, 175)]:
        b1, y, cb, cr = encode_intra(cl, cc, ba, q)
        ph = E.parse_header(b1)
        if ph is None:
            continue
        start, table, _, clean, _ = ph
        if not clean:
            continue
        b2, _, _, _ = encode_intra(cl, cc, bb, q)
        ph2 = E.parse_header(b2)
        if ph2 is None or ph2[1] != table:
            continue
        start2 = ph2[0]
        acbp = read_cbp(y, cb, cr)
        s1, s2 = b1[start:], b2[start2:]
        d = 0
        while d < min(len(s1), len(s2)) and s1[d] == s2[d]:
            d += 1
        for mbt in (s1[:d-1], s1[:d]):
            if 1 <= len(mbt) <= 21:
                others = INTER[table] | known.get(table, set())
                if mbt not in others and not any(o.startswith(mbt) or mbt.startswith(o) for o in others):
                    out.append((table, acbp, mbt))
    return out


if __name__ == "__main__":
    from collections import Counter
    obs = {0: {c: Counter() for c in range(64)}, 1: {c: Counter() for c in range(64)}, 2: {c: Counter() for c in range(64)}}
    known = {0: set(), 1: set(), 2: set()}
    for q in [2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 28, 31]:
        for cbp in range(64):
            for (table, acbp, mbt) in extract_intra(cbp, q, known):
                obs[table][acbp][mbt] += 1
                known[table].add(mbt)
    for t in (0, 1, 2):
        got = {c: obs[t][c].most_common(1)[0][0] for c in range(64) if obs[t][c]}
        miss = [c for c in range(64) if c not in got]
        codes = list(got.values())
        pf = all(not (a != b and b.startswith(a)) for a in codes for b in codes)
        print(f"table {t}: intra {len(got)}/64 missing={miss} prefix_free={pf}")
        json.dump({code: [1, cbp] for cbp, code in got.items()}, open(f"/tmp/wmv2_mbx/mb_intra_t{t}.json", "w"))
