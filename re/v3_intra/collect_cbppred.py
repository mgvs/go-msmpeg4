"""Fit the CBP prediction formula. Diverse content (flat+textured cells) -> varied cbp.
Drive alignment with TRUE cbp (oracle, ap=0 -> unambiguous). For each luma block in ap=0
non-edge MBs record (A=left,B=top,C=topleft coded flags, raw_bit from table, true_cbp_bit).
pred_bit = raw_bit XOR true_cbp_bit. Test candidate formulas f(A,B,C)."""

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


def read_blocks(b, q, cbp4):
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


samples = []  # (A,B,C, raw_bit, true_cbp_bit)


def collect(frame, Yt, q4):
    b = "".join(format(x, "08b") for x in frame)
    mbw = mbh = 8
    p = 17
    coded = np.zeros((2 * mbh, 2 * mbw), int)

    def oac(bx, by):
        C = Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T
        return (
            1
            if any(abs(C[u][v]) >= q4 for u in range(8) for v in range(8) if (u or v))
            else 0
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
            tcbp = [oac(2 * mx + (i % 2), 2 * my + (i // 2)) for i in range(4)]
            if ap == "0":
                end = read_blocks(b, q0, tcbp)
                if end is None:
                    return
                raw = mb_intra[code].split("_")[1]  # cbpy bits
                for i in range(4):
                    bx = 2 * mx + (i % 2)
                    by = 2 * my + (i // 2)
                    if bx > 0 and by > 0:
                        A = coded[by][bx - 1]
                        B = coded[by - 1][bx]
                        C = coded[by - 1][bx - 1]
                        samples.append((A, B, C, int(raw[i]), tcbp[i]))
                for i in range(4):
                    coded[2 * my + (i // 2)][2 * mx + (i % 2)] = tcbp[i]
                p = end
            else:
                chosen = None
                for trial in [tcbp] + [
                    [(c >> (3 - i)) & 1 for i in range(4)] for c in range(16)
                ]:
                    end = read_blocks(b, q0, trial)
                    if end is not None:
                        ok = any(
                            b[end : end + Ln] in mb_intra
                            for Ln in range(1, 14)
                            if end + Ln <= len(b)
                        )
                        if ok:
                            chosen = (trial, end)
                            break
                if chosen is None:
                    return
                for i in range(4):
                    coded[2 * my + (i // 2)][2 * mx + (i % 2)] = chosen[0][i]
                p = chosen[1]


W = H = 128
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
rng = np.arange(W)
done = 0
for t in range(N):
    # diverse: grid of 16x16 cells, each flat(const) or textured
    Y = np.zeros((H, W))
    for cy in range(0, H, 16):
        for cx in range(0, W, 16):
            if (cx // 16 + cy // 16 + t) % 3 == 0:
                Y[cy : cy + 16, cx : cx + 16] = 80 + (cx + cy + t * 7) % 120  # flat
            else:
                ii, jj = np.meshgrid(np.arange(16), np.arange(16), indexing="ij")
                Y[cy : cy + 16, cx : cx + 16] = (
                    110 + 25 * np.sin((ii + t) / 3) + 20 * np.cos((jj + cx) / 4)
                )
    Y = Y.astype(np.uint8)
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
    collect(frame, Yt, EX.config(frame)[0])
    done += 1
    print(f"f{t}: {len(samples)} block-samples")
print(f"\n=== {done} frames, {len(samples)} block-samples. cbp-distribution: ===")
print("true_cbp values:", collections.Counter(s[4] for s in samples))
# test prediction formulas: pred=f(A,B,C); consistency = raw XOR pred == true_cbp
forms = {
    "current (C==B?A:B)": lambda A, B, C: A if C == B else B,
    "(A==C?B:A)": lambda A, B, C: B if A == C else A,
    "top B": lambda A, B, C: B,
    "left A": lambda A, B, C: A,
    "(B==C?A:C)": lambda A, B, C: A if B == C else C,
    "no-pred(0)": lambda A, B, C: 0,
    "AND(A,B)": lambda A, B, C: A & B,
    "OR(A,B)": lambda A, B, C: A | B,
}
for name, f in forms.items():
    ok = sum(1 for A, B, C, raw, tc in samples if (raw ^ f(A, B, C)) == tc)
    print(f"  {name:22}: {ok}/{len(samples)} ({100*ok//max(1,len(samples))}%)")
