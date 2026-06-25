"""Collect cbp=0 acpred=1 top-row blocks (pure AC prediction) from config-0 keyframes
across ALL real DivX3 files in ~/Movies/tests. final = pure prediction -> reverse the rule."""

import subprocess, numpy as np, json, sys, os, struct

sys.path.insert(0, ".")
import recon_loop as R
import extract_div3 as EX

mbi = json.load(open("data/table_mb_intra_raw.json"))
mb_intra = {v: k for k, v in mbi.items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
rlc = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_chroma.json")).items()
}
c2l = {v: k for k, v in rl.items()}
c2c = {v: k for k, v in rlc.items()}
mxl = {}
mxc = {}
for r, l, la in rl:
    mxl[(la, r)] = max(mxl.get((la, r), 0), l)
for r, l, la in rlc:
    mxc[(la, r)] = max(mxc.get((la, r), 0), l)
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
alt_h = [
    0,
    1,
    2,
    3,
    8,
    9,
    16,
    17,
    10,
    11,
    4,
    5,
    6,
    7,
    15,
    14,
    13,
    12,
    19,
    18,
    24,
    25,
    32,
    33,
    26,
    27,
    20,
    21,
    22,
    23,
    28,
    29,
    30,
    31,
    34,
    35,
    40,
    41,
    48,
    49,
    42,
    43,
    36,
    37,
    38,
    39,
    44,
    45,
    46,
    47,
    50,
    51,
    56,
    57,
    58,
    59,
    52,
    53,
    54,
    55,
    60,
    61,
    62,
    63,
]


def dect(b, p, ch=False):
    cm = c2c if ch else c2l
    ml = mxc if ch else mxl
    esc = "1011010" if ch else "0000011"
    if b[p : p + 7] == esc:
        q = p + 7
        if b[q] == "1":
            q += 1
            m = 1
        elif b[q : q + 2] == "01":
            q += 2
            m = 2
        else:
            q += 2
            last = int(b[q])
            run = int(b[q + 1 : q + 7], 2)
            lv = int(b[q + 7 : q + 15], 2)
            return (run, lv - 256 if lv >= 128 else lv, last), (q + 15) - p
        for L in range(1, 17):
            if b[q : q + L] in cm:
                rl_ = cm[b[q : q + L]]
                break
        else:
            return None
        run, lev, last = rl_
        q += L
        sign = b[q]
        q += 1
        if m == 1:
            lev += ml.get((last, run), 0)
        return (run, -lev if sign == "1" else lev, last), q - p
    for L in range(1, 17):
        if b[p : p + L] in cm:
            r = cm[b[p : p + L]]
            return (r[0], -r[1] if b[p + L] == "1" else r[1], r[2]), L + 1
    return None


def dcdec(b, p, tab):
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n
    return None, 0


def dcs(q):
    return 8 if q <= 4 else (2 * q if q <= 8 else (q + 8 if q <= 24 else 2 * q - 16))


# skeleton cache per (W,H)
_sk = {}


def oracle(frame, W, H):
    if (W, H) not in _sk:
        Y = (np.arange(W * H).reshape(H, W) * 7 % 256).astype(np.uint8)
        U = (np.arange(W * H // 4).reshape(H // 2, W // 2) * 13 % 256).astype(np.uint8)
        V = U.copy()
        open("/tmp/q.yuv", "wb").write(Y.tobytes() + U.tobytes() + V.tobytes())
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
                "/tmp/q.yuv",
                "-c:v",
                "msmpeg4",
                "-qscale:v",
                "2",
                "-frames:v",
                "1",
                "-vtag",
                "DIV3",
                "/tmp/q.avi",
            ]
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                "/tmp/q.avi",
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-frames:v",
                "1",
                "-f",
                "data",
                "/tmp/qf.bin",
            ]
        )
        skf = open("/tmp/qf.bin", "rb").read()
        skavi = bytearray(open("/tmp/q.avi", "rb").read())
        _sk[(W, H)] = (skf, skavi, skavi.find(skf))
    skf, skavi, skoff = _sk[(W, H)]
    if len(frame) > len(skf):
        return None
    a = bytearray(skavi)
    a[skoff : skoff + len(skf)] = frame + skf[len(frame) :]
    open("/tmp/qo.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/qo.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    return o[: W * H] if len(o) >= W * H else None


def collect(frame, W, H):
    b = "".join(format(x, "08b") for x in frame)
    q = int(b[2:7], 2)
    ds = dcs(q)
    defv = 1024 // ds
    mbw, mbh = W // 16, H // 16
    oy = oracle(frame, W, H)
    if oy is None:
        return []
    Yt = np.frombuffer(oy[: W * H], np.uint8).reshape(H, W).astype(float)

    def oc(bx, by):
        if by * 8 + 8 > H or bx * 8 + 8 > W:
            return None
        C = Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T

        def L(F):
            return 0 if abs(F) < q else int(np.sign(F) * round((abs(F) / q - 1) / 2))

        return {(u, v): L(C[u][v]) for u in range(8) for v in range(8)}

    p = 17
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    out = []
    try:
        for my in range(mbh):
            for mx in range(mbw):
                m = None
                for Ln in range(1, 14):
                    if b[p : p + Ln] in mb_intra:
                        m = b[p : p + Ln]
                        break
                if m is None:
                    return out
                cbpk = mb_intra[m]
                p += len(m)
                cbcr, cbpy = cbpk.split("_")
                raw = [int(cbpy[i]) for i in range(4)] + [int(cbcr[0]), int(cbcr[1])]
                cbp = [0] * 6
                for i in range(4):
                    bx = 2 * mx + (i % 2)
                    by = 2 * my + (i // 2)
                    A = codedL[by][bx - 1] if bx > 0 else 0
                    Bb = codedL[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                    Cc = codedL[by - 1][bx] if by > 0 else 0
                    cbp[i] = raw[i] ^ (A if Bb == Cc else Cc)
                    codedL[by][bx] = cbp[i]
                cbp[4] = raw[4]
                cbp[5] = raw[5]
                ap = b[p]
                p += 1
                for blk in range(6):
                    tab = dctab_l if blk < 4 else dctab_c
                    diff, n = dcdec(b, p, tab)
                    if diff is None:
                        return out
                    p += n
                    if cbp[blk]:
                        while True:
                            r = dect(b, p, blk >= 4)
                            if r is None:
                                return out
                            (run, lev, last), ln = r
                            p += ln
                            if last:
                                break
                    if blk < 4 and blk in (0, 1) and ap == "1" and cbp[blk] == 0:
                        bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                        if bx > 0 and by > 0:
                            fin = oc(bx, by)
                            bbb = oc(bx - 1, by - 1)
                            cc = oc(bx, by - 1)
                            aa = oc(bx - 1, by)
                            if fin and bbb and cc and aa:
                                fld = abs(aa[(0, 0)] - bbb[(0, 0)]) > abs(
                                    bbb[(0, 0)] - cc[(0, 0)]
                                )
                                F = {k: v for k, v in fin.items() if v and k != (0, 0)}
                                rowf = {vv: F.get((0, vv), 0) for vv in range(1, 8)}
                                colf = {uu: F.get((uu, 0), 0) for uu in range(1, 8)}
                                interior = any(k[0] > 0 and k[1] > 0 for k in F)
                                crow = {vv: cc.get((0, vv), 0) for vv in range(1, 8)}
                                acol = {uu: aa.get((uu, 0), 0) for uu in range(1, 8)}
                                if fld:
                                    std = (not F) or (
                                        colf == acol
                                        and all(v == 0 for v in rowf.values())
                                        and not interior
                                    )
                                else:
                                    std = (not F) or (
                                        rowf == crow
                                        and all(v == 0 for v in colf.values())
                                        and not interior
                                    )
                                if (
                                    not std
                                ):  # FIRST quirk block -> clean point, record + stop frame
                                    out.append(
                                        dict(
                                            fl=fld,
                                            interior=interior,
                                            final=F,
                                            bb={
                                                k: v
                                                for k, v in bbb.items()
                                                if v and k != (0, 0)
                                            },
                                            c={
                                                k: v
                                                for k, v in cc.items()
                                                if v and k != (0, 0)
                                            },
                                            a={
                                                k: v
                                                for k, v in aa.items()
                                                if v and k != (0, 0)
                                            },
                                        )
                                    )
                                    return out
    except Exception:
        return out
    return out


allpts = []
for fn in sorted(f for f in os.listdir(os.path.expanduser("~/Movies/tests")) if f.endswith(".avi")):
    path = os.path.expanduser(f"~/Movies/tests/{fn}")
    import subprocess as sp

    wh = sp.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_tag_string",
            "-of",
            "csv=p=0",
            path,
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if "DIV3" not in wh:
        continue
    nums = [int(x) for x in wh.split(",") if x.isdigit()]
    if len(nums) < 2:
        continue
    W, H = nums[0], nums[1]
    ifr = EX.iframes(path, maxf=40)
    n0 = 0
    for fr in ifr:
        c = EX.config(fr)
        if c[1] == 1 and c[2] == 2 and c[3] == 1:
            pts = collect(fr, W, H)
            allpts += pts
            n0 += len(pts)
    print(f"{fn:40} {W}x{H}: +{n0} cbp=0 quirk-candidates")
print(f"\nCLEAN first-quirk points: {len(allpts)}")


def col(d):
    return {u: d.get((u, 0), 0) for u in range(1, 8)}


def row(d):
    return {v: d.get((0, v), 0) for v in range(1, 8)}


clean = [d for d in allpts if not d["interior"]]
print(f"  no-interior (clean row/col): {len(clean)} of {len(allpts)}")
fa = [d for d in clean if d["fl"]]
fc_ = [d for d in clean if not d["fl"]]
print(f"  from-left:{len(fa)} from-above:{len(fc_)}")


def test(name, blocks, pred):
    ok = sum(1 for d in blocks if pred(d))
    print(f"    {name}: {ok}/{len(blocks)}")


print("from-ABOVE quirk (final-row vs sources):")
test("== bb-row(topleft)", fc_, lambda d: row(d["final"]) == row(d["bb"]))
test(
    "== avg(bb,c)-row",
    fc_,
    lambda d: row(d["final"])
    == {v: (row(d["bb"])[v] + row(d["c"])[v]) // 2 for v in range(1, 8)},
)
test("== a-row(left)", fc_, lambda d: row(d["final"]) == row(d["a"]))
print("from-LEFT quirk (final-col vs sources):")
test("== bb-col(topleft)", fa, lambda d: col(d["final"]) == col(d["bb"]))
test(
    "== avg(a,bb)-col",
    fa,
    lambda d: col(d["final"])
    == {u: (col(d["a"])[u] + col(d["bb"])[u]) // 2 for u in range(1, 8)},
)
test("== c-col(top)", fa, lambda d: col(d["final"]) == col(d["c"]))


def col(d):
    return {u: d.get((u, 0), 0) for u in range(1, 8)}


def row(d):
    return {v: d.get((0, v), 0) for v in range(1, 8)}


sig = [d for d in clean if sum(abs(v) for v in d["final"].values()) >= 4]
print(f"\nSIGNIFICANT clean first-quirk: {len(sig)}")
fcS = [d for d in sig if not d["fl"]]
faS = [d for d in sig if d["fl"]]
print(f"from-above sig:{len(fcS)} from-left sig:{len(faS)}")
print("from-ABOVE: final-row, c-row, DELTA(final-c), and where delta matches:")
for d in fcS[:12]:
    fr = row(d["final"])
    cr = row(d["c"])
    delta = {v: fr[v] - cr[v] for v in range(1, 8)}
    bbc = col(d["bb"])
    ac = col(d["a"])
    bbr = row(d["bb"])
    print(
        f"  fr={[fr[v] for v in range(1,5)]} cr={[cr[v] for v in range(1,5)]} D={[delta[v] for v in range(1,5)]} | bb-col={[bbc[u] for u in range(1,5)]} bb-row={[bbr[v] for v in range(1,5)]}"
    )
