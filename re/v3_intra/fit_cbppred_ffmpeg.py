"""Fit CBP prediction via ffmpeg (fast, clean, no DivX3). ffmpeg always: ap=0, rlt=2/rlc=1
(tables we have). Diverse cbp content (flat + gentle-gradient 8x8 cells). All MBs ap=0 =>
true_cbp = oracle AC presence (unambiguous). Collect (A,B,C coded flags, raw_bit, true_cbp)
per non-edge luma block; test prediction formulas raw XOR f(A,B,C) == true_cbp."""

import subprocess, numpy as np, json, sys, collections

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


def enc_dec(Y, W, H, qs=4):
    open("/tmp/c.yuv", "wb").write(
        Y.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes()
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-s",
            f"{W}x{H}",
            "-i",
            "/tmp/c.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            str(qs),
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "/tmp/c.avi",
        ],
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            "/tmp/c.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            "/tmp/c.bin",
        ],
        stderr=subprocess.DEVNULL,
    )
    b = "".join(format(x, "08b") for x in open("/tmp/c.bin", "rb").read())
    oy = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/c.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    return b, np.frombuffer(oy[: W * H], np.uint8).reshape(H, W).astype(float)


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


samples = []


def collect(b, Yt, W, H, q4):
    mbw, mbh = W // 16, H // 16
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
            q = p + L + 1
            tcbp = [oac(2 * mx + (i % 2), 2 * my + (i // 2)) for i in range(4)]
            # chroma flat? (grayscale) -> cbp chroma 0
            raw = mb_intra[code].split("_")[1]
            for i in range(4):
                bx, by = 2 * mx + (i % 2), 2 * my + (i // 2)
                if bx > 0 and by > 0:
                    A = coded[by][bx - 1]
                    B = coded[by - 1][bx]
                    C = coded[by - 1][bx - 1]
                    samples.append((A, B, C, int(raw[i]), tcbp[i]))
            # advance: read 6 blocks with tcbp (chroma cbp=0)
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                d, n = dclen(b, q, tab)
                if d is None:
                    return
                q += n
                if blk < 4 and tcbp[blk]:
                    while True:
                        r = aclen(b, q)
                        if r is None:
                            return
                        last, ln = r
                        q += ln
                        if last:
                            break
            for i in range(4):
                coded[2 * my + (i // 2)][2 * mx + (i % 2)] = tcbp[i]
            p = q


W = H = 128
np.random.seed(0)
for t in range(8):
    Y = np.zeros((H, W))
    for cy in range(0, H, 8):
        for cx in range(0, W, 8):
            r = (cx * 7 + cy * 13 + t * 101) % 5
            if r < 2:
                Y[cy : cy + 8, cx : cx + 8] = (
                    70 + (cx + cy + t * 9) % 140
                )  # flat -> cbp=0
            else:
                ii, jj = np.meshgrid(np.arange(8), np.arange(8), indexing="ij")
                Y[cy : cy + 8, cx : cx + 8] = (
                    100 + (cx % 50) * 0.2 + ii * 1.5 + jj * 1.0
                )  # gentle gradient -> cbp=1, stays smooth
    Y = np.clip(Y, 0, 255).astype(np.uint8)
    b, Yt = enc_dec(Y, W, H)

    # verify config rlt=2
    def c3(s, pp):
        return (0, 1) if s[pp] == "0" else ((1 if s[pp + 1] == "0" else 2), 2)

    pp = 12
    rc, n = c3(b, pp)
    pp += n
    rt, _ = c3(b, pp)
    if rt != 2:
        print(f"t{t}: rlt={rt} skip")
        continue
    collect(b, Yt, W, H, 4)
    print(
        f"t{t}: rlt=2, {len(samples)} samples, cbp-dist={collections.Counter(s[4] for s in samples)}"
    )
print(
    f"\n=== {len(samples)} samples. cbp values: {collections.Counter(s[4] for s in samples)} ==="
)
forms = {
    "current(C==B?A:B)": lambda A, B, C: A if C == B else B,
    "(A==C?B:A)": lambda A, B, C: B if A == C else A,
    "top B": lambda A, B, C: B,
    "left A": lambda A, B, C: A,
    "(B==C?A:C)": lambda A, B, C: A if B == C else C,
    "no-pred": lambda A, B, C: 0,
    "AND": lambda A, B, C: A & B,
    "OR": lambda A, B, C: A | B,
    "(A==B?C:B)": lambda A, B, C: C if A == B else B,
    "MAX(A,B,C)→": lambda A, B, C: 1 if (A + B + C) >= 2 else 0,
}
for name, f in forms.items():
    ok = sum(1 for A, B, C, raw, tc in samples if (raw ^ f(A, B, C)) == tc)
    print(f"  {name:20}: {ok}/{len(samples)} ({100*ok//max(1,len(samples))}%)")
