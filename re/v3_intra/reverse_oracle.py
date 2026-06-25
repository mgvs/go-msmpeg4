"""Config-agnostic oracle-guided RL reversal on dc=1 frames. multi-block frames, pos&neg.
For acpred=0 coded luma blocks (coded==final, no AC prediction): ffmpeg oracle -> quantized
coeffs in zigzag -> (run,level,last) sequence (K coeffs). K successive pos/neg diffs = K
sign bits -> split K codes. Map code->(run,level,last) per rlt. dc=1 only (DC table known,
q-independent). Skip escape codes. Accumulate over many frames."""

import subprocess, numpy as np, json, sys, pickle, collections

sys.path.insert(0, ".")
import divx_batch as DB, extract_div3 as EX, recon_loop as R

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
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
ZZ = [(0, 0)] + R.ZZ_AC  # full zigzag incl DC at 0


def dclen(b, p, tab):
    for n in range(1, 36):
        if b[p : p + n] in tab:
            return n
    return 0


def seq_from_oracle(C, q):
    """quantized AC coeffs in zigzag -> (run,level,last) list (level signed magnitude)."""
    levels = []
    for idx in range(1, 64):
        u, v = ZZ[idx]
        a = abs(C[u, v])
        L = int(round((a / q - 1) / 2)) if a >= q else 0
        levels.append(L * (1 if C[u, v] > 0 else -1))
    seq = []
    run = 0
    for i, L in enumerate(levels):
        if L == 0:
            run += 1
        else:
            seq.append([run, abs(L)])
            run = 0
    if not seq:
        return []
    out = [(r, l, 0) for r, l in seq[:-1]] + [(seq[-1][0], seq[-1][1], 1)]
    return out


tables = collections.defaultdict(dict)
esc = "0000011"


def oracle_decode(frame, W, H):
    key = (W, H)
    if key not in oracle_decode.sk:
        Yg = (np.arange(W * H).reshape(H, W) % 200 + 20).astype(np.uint8)
        open("/tmp/sk.yuv", "wb").write(
            Yg.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes()
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
                "/tmp/sk.yuv",
                "-c:v",
                "msmpeg4",
                "-qscale:v",
                "4",
                "-frames:v",
                "1",
                "-vtag",
                "DIV3",
                "/tmp/sk.avi",
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
                "/tmp/sk.avi",
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-frames:v",
                "1",
                "-f",
                "data",
                "/tmp/skf.bin",
            ],
            stderr=subprocess.DEVNULL,
        )
        skf = open("/tmp/skf.bin", "rb").read()
        av = bytearray(open("/tmp/sk.avi", "rb").read())
        oracle_decode.sk[key] = (skf, av, av.find(skf))
    skf, av, off = oracle_decode.sk[key]
    if len(frame) > len(skf):
        return None
    a = bytearray(av)
    a[off : off + len(skf)] = frame + skf[len(frame) :]
    open("/tmp/ro.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/ro.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    return (
        np.frombuffer(o[: W * H], np.uint8).reshape(H, W).astype(float)
        if len(o) >= W * H
        else None
    )


oracle_decode.sk = {}


def process(fp, fn, W, H):
    if fp is None or fn is None:
        return
    cfg = EX.config(fp)
    if cfg != EX.config(fn) or cfg[3] != 1:
        return
    q = cfg[0]
    rlt = cfg[2]
    Yt = oracle_decode(fp, W, H)
    if Yt is None:
        return
    bp = "".join(format(x, "08b") for x in fp)
    bn = "".join(format(x, "08b") for x in fn)
    mbw, mbh = W // 16, H // 16
    p = 17
    coded = np.zeros((2 * mbh, 2 * mbw), int)

    def fd_from(s):
        k = s
        while k < len(bp) and k < len(bn) and bp[k] == bn[k]:
            k += 1
        return k

    for my in range(mbh):
        for mx in range(mbw):
            Lc = None
            for Ln in range(1, 14):
                if bp[p : p + Ln] in mb_intra:
                    Lc = Ln
                    break
            if Lc is None:
                return
            raw = mb_intra[bp[p : p + Lc]]
            p += Lc
            cbcr, cbpy = raw.split("_")
            rawb = [int(cbpy[i]) for i in range(4)]
            cbp = [0] * 4
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = coded[by][bx - 1] if bx > 0 else 0
                B = coded[by - 1][bx] if by > 0 else 0
                Cc = coded[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                cbp[i] = rawb[i] ^ (A if Cc == B else B)
                coded[by][bx] = cbp[i]
            ap = bp[p]
            p += 1
            if ap == "1" and any(cbp):
                return  # acpred=1 coded: oracle!=coded, can't advance -> stop
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                n = dclen(bp, p, tab)
                if n == 0:
                    return
                p += n
                isc = cbp[blk] if blk < 4 else int(cbcr[blk - 4])
                if not isc:
                    continue
                if (
                    blk >= 4
                ):  # chroma: skip parsing (different table); can't advance -> abort frame
                    return
                bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                C = Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T
                seq = seq_from_oracle(C, q)
                if not seq:
                    return  # cbp says coded but oracle empty -> misalign, abort
                # extract K codes via K successive sign diffs
                for run, lev, last in seq:
                    if bp[p : p + 7] == esc:
                        return  # escape coef: can't advance without table -> stop frame
                    fd = fd_from(p)
                    code = bp[p:fd]
                    if not code:
                        return
                    if (
                        ap == "0"
                        and not code.startswith(esc)
                        and run < 63
                        and 1 <= lev <= 40
                    ):
                        tables[(cfg[1], rlt)][(run, lev, last)] = code
                    p = fd + 1
    return cfg


W = H = 128
NB = int(sys.argv[1]) if len(sys.argv) > 1 else 40


def mk(t, sign):
    pat = np.fromfunction(
        lambda i, j: ((i * (2 + t % 4) + j * (1 + t % 3) + t * 5) % 50 - 25)
        + ((i * j) % (7 + t % 5) - 3),
        (H, W),
    )
    pat = np.round(pat).astype(int)  # integer pattern
    Y = np.clip(128 + sign * pat, 16, 240).astype(np.uint8)
    return Y


posf = [mk(t, 1) for t in range(NB)]
negf = [mk(t, -1) for t in range(NB)]
print(f"encoding {NB} pos+neg (128x128)...")
fp = DB.encode_batch(posf, W, H)
fn = DB.encode_batch(negf, W, H)
cfgs = collections.Counter()
for i in range(NB):
    c = process(fp[i], fn[i], W, H)
    if c:
        cfgs[c[1:]] += 1
print("processed configs:", dict(cfgs))
for k, v in sorted(tables.items()):
    codes = list(v.values())
    coll = sum(1 for a in codes for c in codes if a != c and a.startswith(c))
    print(f"  rlc{k[0]}rlt{k[1]}: {len(v)} codes, collisions={coll}")
pickle.dump(
    {
        str(k): {f"{r},{l},{la}": c for (r, l, la), c in v.items()}
        for k, v in tables.items()
    },
    open("/tmp/rl_oracle.pkl", "wb"),
)
