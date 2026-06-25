"""Determine cbp coding: is there CBP prediction or is cbp=raw directly?
Oracle-driven alignment: for ap=0 MBs true_cbp = oracle AC presence (unambiguous, no
pred); read AC for those blocks to stay aligned. For ap=1, search cbp that parses+aligns.
Record mb_code->true_cbp for ap=0 non-edge MBs; check if consistent (one cbp per code).
"""

import subprocess, numpy as np, json, sys, collections

sys.path.insert(0, ".")
import divx_encode as DE, extract_div3 as EX

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
c2l = {v: k for k, v in rl.items()}
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
    for n in range(1, 36):
        if b[p : p + n] in tab:
            return tab[b[p : p + n]], n
    return None, 0


def aclen(b, p):
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


def read_blocks(b, q, cbp4):  # chroma cbp=0 (grayscale); returns end q or None
    for blk in range(6):
        tab = dctab_l if blk < 4 else dctab_c
        d, n = dclen(b, q, tab)
        if d is None:
            return None
        q += n
        if blk < 4 and cbp4[blk]:
            while True:
                r = aclen(b, q)
                if r is None:
                    return None
                last, ln = r
                q += ln
                if last:
                    break
    return q


def collect(frame, Yt, q4, stats, bad):
    b = "".join(format(x, "08b") for x in frame)
    mbw = mbh = 8
    p = 17

    def oac(bx, by):
        C = Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T
        return (
            [1 if abs(C[u][v]) >= q4 else 0 for u in range(1, 64) for _ in [0]][0]
            if False
            else (
                1
                if any(
                    abs(C[u][v]) >= q4 for u in range(8) for v in range(8) if (u or v)
                )
                else 0
            )
        )

    for my in range(mbh):
        for mx in range(mbw):
            L = None
            for Ln in range(1, 14):
                if b[p : p + Ln] in mb_intra:
                    L = Ln
                    break
            if L is None:
                return
            code = b[p : p + L]
            ap = b[p + L]
            q0 = p + L + 1
            truecbp = [oac(2 * mx + (i % 2), 2 * my + (i // 2)) for i in range(4)]
            if ap == "0":
                end = read_blocks(b, q0, truecbp)
                if end is None:
                    return
                if 0 < mx and 0 < my:  # non-edge: record
                    stats[(code, tuple(truecbp))] += 1
                p = end
            else:  # ap=1: search cbp that parses (prefer truecbp-ish); just need alignment
                chosen = None
                # try truecbp first (predicted AC means cbp could be 0 even with oracle AC; try subsets)
                for trial in [truecbp] + [
                    [(cbpy >> (3 - i)) & 1 for i in range(4)] for cbpy in range(16)
                ]:
                    end = read_blocks(b, q0, trial)
                    if end is not None:
                        # check next MB mb_code valid (alignment sanity)
                        nxt = False
                        for Ln in range(1, 14):
                            if end + Ln <= len(b) and b[end : end + Ln] in mb_intra:
                                nxt = True
                                break
                        if nxt:
                            chosen = (trial, end)
                            break
                if chosen is None:
                    return
                p = chosen[1]


W = H = 128
stats = collections.Counter()
bad = []
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
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
    collect(frame, Yt, EX.config(frame)[0], stats, bad)
    done += 1
    print(f"f{t}: {sum(stats.values())} ap=0 obs, {len(stats)} (code,cbp) pairs")
# consistency: does each code map to ONE cbp?
print(f"\n=== {done} frames. CONSISTENCY of code->cbp (no-prediction hypothesis) ===")
code2cbps = collections.defaultdict(collections.Counter)
for (code, cbp), n in stats.items():
    code2cbps[code][cbp] += n
incons = 0
for code in sorted(code2cbps, key=len):
    cbps = code2cbps[code]
    raw = mb_intra.get(code, "?")
    if len(cbps) > 1:
        incons += 1
        print(f"  {code:8} -> INCONSISTENT {dict(cbps)} (table raw={raw})")
    else:
        cbp = list(cbps)[0]
        print(f"  {code:8} -> cbp={cbp} n={cbps[cbp]} (table raw={raw})")
print(f"\n{len(code2cbps)} codes, {incons} INCONSISTENT.")
print("If 0 inconsistent -> cbp=raw, NO prediction; table = code->cbp directly.")
