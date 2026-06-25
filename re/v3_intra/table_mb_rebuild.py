"""Rebuild table_mb_intra (code->raw) from controlled DivX3 frames via oracle-constrained
search. For each MB, brute-force (mb_code_len L, luma cbpy) such that the MB fully parses
(6 valid DCs + valid ACs for coded blocks) AND, for acpred=0 MBs, reconstructed DC
(pred=SELECT + diff) matches the ffmpeg oracle. Alignment doesn't trust the mb_code table.
Grayscale => chroma cbp=0 (cbcr=00). Records code->raw for acpred=0 MBs -> clean rebuild.
"""

import subprocess, numpy as np, json, sys, pickle, collections

sys.path.insert(0, ".")
import divx_encode as DE, extract_div3 as EX

dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
c2l = {v: k for k, v in rl.items()}
mbi_old = json.load(open("data/table_mb_intra_raw.json"))  # raw_str -> code
old_code2raw = {v: k for k, v in mbi_old.items()}
Mm = np.array(
    [
        [
            0.5
            * ((1 / np.sqrt(2)) if k == 0 else 1)
            * np.cos((2 * n + 1) * k * np.pi / 16)
            for n in range(8)
        ]
        for k in range(8)
    ]
)


def dclen(b, p, tab):
    c = ""
    for n in range(1, 36):
        c = b[p : p + n]
        if c in tab:
            return tab[c], n
    return None, 0


def aclen(b, p):  # returns (last, nbits) or None
    if b[p : p + 7] == "0000011":
        q = p + 7
        if b[q] == "1":
            q += 1
        elif b[q : q + 2] == "01":
            q += 2
        else:
            return int(b[q + 2]), (q + 2 + 1 + 6 + 8) - p
        for L in range(1, 17):
            if b[q : q + L] in c2l:
                return c2l[b[q : q + L]][2], (q + L + 1) - p
        return None
    for L in range(1, 17):
        if b[p : p + L] in c2l:
            return c2l[b[p : p + L]][2], L + 1
    return None


def parse_blocks(b, q, cbp4):
    """parse 6 blocks (4 luma cbp4, chroma cbp0) from q; return (end_q, dcdiffs) or None"""
    diffs = []
    for blk in range(6):
        tab = dctab_l if blk < 4 else dctab_c
        d, n = dclen(b, q, tab)
        if d is None:
            return None
        q += n
        diffs.append(d)
        if blk < 4 and cbp4[blk]:
            while True:
                r = aclen(b, q)
                if r is None:
                    return None
                last, ln = r
                q += ln
                if last:
                    break
    return q, diffs


def odc(Yt, bx, by, q4):
    return round((Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T)[0][0] / 8)


def rebuild_frame(frame, Yt, q4, code2raw, stats):
    b = "".join(format(x, "08b") for x in frame)
    mbw = mbh = 8
    dcL = np.full((2 * mbh, 2 * mbh), -999)
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    p = 17
    for my in range(mbh):
        for mx in range(mbw):
            o = [odc(Yt, 2 * mx + (i % 2), 2 * my + (i // 2), q4) for i in range(4)]
            # mb_code LENGTH from current table (prefix-match); fall back search if missing
            L = None
            for Ln in range(1, 14):
                if b[p : p + Ln] in code2raw:
                    L = Ln
                    break
            if L is None:
                return
            code = b[p : p + L]
            ap = b[p + L]
            q0 = p + L + 1
            # search cbpy (chroma=0), score by oracle DC closeness
            best = None
            for cbpy in range(16):
                cbp4 = [(cbpy >> (3 - i)) & 1 for i in range(4)]
                r = parse_blocks(b, q0, cbp4)
                if r is None:
                    continue
                qend, diffs = r
                tmp = dcL.copy()
                err = 0
                edge = False
                for i in range(4):
                    bx, by = 2 * mx + (i % 2), 2 * my + (i // 2)
                    a = tmp[by][bx - 1] if bx > 0 else -999
                    bb = tmp[by - 1][bx - 1] if (bx > 0 and by > 0) else -999
                    cc = tmp[by - 1][bx] if by > 0 else -999
                    if a == -999 or bb == -999 or cc == -999:
                        edge = True
                        tmp[by][bx] = o[i]
                        continue
                    fl = abs(a - bb) > abs(bb - cc)
                    pred = a if fl else cc
                    err += abs(pred + diffs[i] - o[i])
                    tmp[by][bx] = o[i]
                # for ap=0 tight (<=8 total), ap=1 loose (quirk) (<=120)
                lim = 8 if ap == "0" else 120
                if not edge and err <= lim:
                    if best is None or err < best[0]:
                        best = (err, cbpy, qend, cbp4)
                elif edge:  # can't constrain; keep as weak candidate (shortest valid)
                    if best is None:
                        best = (9999, cbpy, qend, cbp4)
            if best is None:
                return
            err, cbpy, qend, cbp4 = best
            raw = f"00_{cbpy:04b}"
            if ap == "0" and err <= 2:  # clean confident record
                stats[(code, raw)] += 1
            # update grids with oracle DC + coded flags
            for i in range(4):
                bx, by = 2 * mx + (i % 2), 2 * my + (i // 2)
                dcL[by][bx] = o[i]
                codedL[by][bx] = cbp4[i]
            p = qend


W = H = 128
stats = collections.Counter()
N = int(sys.argv[1]) if len(sys.argv) > 1 else 16
pats = [
    lambda i, j, t: 90 + i * 1.3 + j * 0.5 + t * 5,
    lambda i, j, t: 128 + 50 * np.sin((i + t * 3) / 9),
    lambda i, j, t: 100 + i * 1.5 + 18 * np.cos(j / 6 + t),
    lambda i, j, t: 115 + j * 1.4 + t * 4,
    lambda i, j, t: 128 + 55 * np.sin(i / 7) * np.cos(j / 9 + t),
    lambda i, j, t: 70 + i * 0.7 + j * 0.9 + 30 * np.sin(j / 5 + t),
]
done = 0
for t in range(N):
    Y = np.fromfunction(lambda i, j: pats[t % len(pats)](i, j, t), (H, W)).astype(
        np.uint8
    )
    frame = DE.encode(Y.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(), W, H)
    if frame is None or EX.config(frame)[2] != 2:
        print(f"f{t}:skip")
        continue
    oy = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/de_out.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    if len(oy) < W * H:
        continue
    Yt = np.frombuffer(oy[: W * H], np.uint8).reshape(H, W).astype(float)
    rebuild_frame(frame, Yt, EX.config(frame)[0], old_code2raw, stats)
    done += 1
    print(f"f{t}: stats now {len(stats)} (code,raw) pairs")
# build code->raw (majority), compare to old
print(
    f"\n=== rebuilt from {done} frames, {sum(stats.values())} acpred=0 observations ==="
)
code2raw = {}
for (code, raw), n in stats.items():
    if code not in code2raw or stats[(code, code2raw[code])] < n:
        code2raw[code] = raw
disc = 0
for code, raw in sorted(code2raw.items(), key=lambda x: len(x[0])):
    old = old_code2raw.get(code, "MISSING")
    mark = "" if old == raw else f"  <-- OLD={old} DIFFERS"
    if old != raw:
        disc += 1
    print(f"  {code:13} -> {raw} (n={stats[(code,raw)]}){mark}")
print(f"\n{len(code2raw)} codes observed, {disc} differ from current table")
pickle.dump(dict(code2raw), open("/tmp/mb_rebuild.pkl", "wb"))
