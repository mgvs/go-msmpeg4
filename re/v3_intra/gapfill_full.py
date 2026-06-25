"""Combined luma+chroma gap-fill: run reconstruction, on each AC-fail reverse the
missing code via decoder_oracle (luma base.avi / chroma cbase.avi), add to the proper
table, repeat until the real frame fully decodes."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

mbi = json.load(open("data/table_mb_intra_raw.json"))
mb_intra = {v: k for k, v in mbi.items()}
DCL = json.load(open("data/dc_luma.json"))
DCC = json.load(open("data/dc_chroma.json"))
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
rlc = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_chroma.json")).items()
}
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
ZZ = R.ZZ_AC


# oracles
def setup_oracle(avi, binf):
    bb = open(binf, "rb").read()
    N = len(bb)
    avib = bytearray(open(avi, "rb").read())
    off = avib.find(bb)
    return avib, off, N


luma_avi, luma_off, luma_N = setup_oracle(
    "/tmp/msm_craft/base.avi", "/tmp/msm_craft/base.bin"
)
chr_avi, chr_off, chr_N = setup_oracle("/tmp/cbase.avi", "/tmp/cbase.bin")
luma_hdr = "".join(
    format(x, "08b") for x in open("/tmp/msm_craft/base.bin", "rb").read()
)[:17]
chr_prefix = open("/tmp/cbase_prefix.txt").read()


def coefs_luma(ac):
    bits = luma_hdr + mbi["00_1000"] + "0" + "10" + ac
    while len(bits) % 8:
        bits += "1"
    bb = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
    while len(bb) < luma_N:
        bb.append(0)
    a = bytearray(luma_avi)
    a[luma_off : luma_off + luma_N] = bytes(bb[:luma_N])
    open("/tmp/o.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/o.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    if len(o) < 256:
        return []
    C = (
        Mm
        @ (np.frombuffer(o[:256], np.uint8).reshape(16, 16)[:8, :8].astype(float) - 128)
        @ Mm.T
    )
    return [
        (
            next((i for i, (a, b) in enumerate(ZZ) if (a, b) == (u, v)), -1),
            max(1, round((abs(C[u][v]) / 4 - 1) / 2)),
            C[u][v],
        )
        for u in range(8)
        for v in range(8)
        if (u or v) and abs(C[u][v]) >= 2.5
    ]


def coefs_chroma(ac):
    bits = chr_prefix + ac
    while len(bits) % 8:
        bits += "1"
    bb = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
    while len(bb) < chr_N:
        bb.append(0)
    a = bytearray(chr_avi)
    a[chr_off : chr_off + chr_N] = bytes(bb[:chr_N])
    open("/tmp/o.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/o.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    if len(o) < 384:
        return []
    C = (
        Mm
        @ (np.frombuffer(o[256:320], np.uint8).reshape(8, 8).astype(float) - 128)
        @ Mm.T
    )
    return [
        (
            next((i for i, (a, b) in enumerate(ZZ) if (a, b) == (u, v)), -1),
            max(1, round((abs(C[u][v]) / 4 - 1) / 2)),
            C[u][v],
        )
        for u in range(8)
        for v in range(8)
        if (u or v) and abs(C[u][v]) >= 2.5
    ]


def reverse_code(failbits, chroma, existing):
    cf = coefs_chroma if chroma else coefs_luma
    c0 = cf(failbits[:40] + "0")
    if not c0:
        return None
    dom = max(c0, key=lambda x: abs(x[2]))
    c1 = cf(failbits[:40] + "0" + ("011" if chroma else "0111") + "0")
    last = 0 if len(c1) > len(c0) else 1
    run, lev = dom[0], dom[1]
    # smallest PREFIX-FREE L where coef matches
    for L in range(2, 16):
        cand = failbits[:L]
        if any(cand == e or cand.startswith(e) or e.startswith(cand) for e in existing):
            continue
        cc = cf(cand + "0")
        if cc:
            d2 = max(cc, key=lambda x: abs(x[2]))
            if d2[0] == run and d2[1] == lev:
                return run, lev, last, L
    return run, lev, last, None


# decoder (consume only, find first acfail with block index)
def build_decoder():
    c2l = {v: k for k, v in rl.items()}
    c2c = {v: k for k, v in rlc.items()}
    mll = {}
    mlc = {}
    for r, l, la in rl:
        mll[(la, r)] = max(mll.get((la, r), 0), l)
    for r, l, la in rlc:
        mlc[(la, r)] = max(mlc.get((la, r), 0), l)

    def md(b, p, cm):
        for L in range(1, 17):
            if b[p : p + L] in cm:
                return cm[b[p : p + L]], L
        return None, 0

    def dt(b, p, chroma):
        cm = c2c if chroma else c2l
        ml = mlc if chroma else mll
        esc = "1011010" if chroma else "0000011"
        if b[p : p + 7] == esc:
            q = p + 7
            if b[q] == "1":
                q += 1
                mm = 1
            elif b[q : q + 2] == "01":
                q += 2
                mm = 2
            else:
                q += 2
                last = int(b[q])
                run = int(b[q + 1 : q + 7], 2)
                lv = int(b[q + 7 : q + 15], 2)
                return (run, lv - 256 if lv >= 128 else lv, last), (q + 15) - p, None
            rl_, L = md(b, q, cm)
            if rl_ is None:
                return None, 0, q
            run, lev, last = rl_
            q += L + 1
            if mm == 1:
                lev += ml.get((last, run), 0)
            return (run, lev, last), q - p, None
        rl_, L = md(b, p, cm)
        if rl_ is None:
            return None, 0, p
        return (rl_[0], rl_[1], rl_[2]), L + 1, None

    return dt


def dcdec(b, p, tab):
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n
    return None, 0


def decode(b, mbw, mbh):
    dt = build_decoder()
    sig = R.sig_end(b)
    p = 17
    coded = [[0] * (2 * mbw) for _ in range(2 * mbh)]
    for my in range(mbh):
        for mx in range(mbw):
            m = None
            for L in range(1, 14):
                if b[p : p + L] in mb_intra:
                    m = b[p : p + L]
                    break
            if m is None:
                return ("mb", p, False, my * mbw + mx)
            cbpk = mb_intra[m]
            p += len(m)
            cbcr, cbpy = cbpk.split("_")
            raw = [int(cbpy[i]) for i in range(4)] + [int(cbcr[0]), int(cbcr[1])]
            cbp = [0] * 6
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = coded[by][bx - 1] if bx > 0 else 0
                Bb = coded[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Cc = coded[by - 1][bx] if by > 0 else 0
                cbp[i] = raw[i] ^ (A if Bb == Cc else Cc)
                coded[by][bx] = cbp[i]
            cbp[4] = raw[4]
            cbp[5] = raw[5]
            p += 1
            for blk in range(6):
                tab = DCL if blk < 4 else DCC
                d, n = dcdec(b, p, tab)
                if d is None:
                    return ("dc", p, blk >= 4, my * mbw + mx)
                p += n
                if cbp[blk]:
                    while True:
                        ev, ln, fp = dt(b, p, blk >= 4)
                        if ev is None:
                            return ("ac", fp, blk >= 4, my * mbw + mx)
                        p += ln
                        if ev[2]:
                            break
            if p >= sig:
                return ("done", p, False, my * mbw + mx + 1)
    return ("done", p, False, mbw * mbh)


b = "".join(format(x, "08b") for x in open("/tmp/divx/cfg0.bin", "rb").read())
for it in range(200):
    st, p, chroma, ok = decode(b, 32, 18)
    if st == "done":
        print(f"★★★ FULL FRAME DECODED! {ok} MBs")
        break
    if st != "ac":
        print(f"STOP {st}@{p} after {ok} MBs (chroma={chroma})")
        break
    fb = b[p : p + 40]
    rv = reverse_code(fb, chroma, set((rlc if chroma else rl).values()))
    if rv is None or rv[3] is None:
        print(f"rev-fail @{p} chroma={chroma}: {fb[:16]} (ok {ok})")
        break
    run, lev, last, L = rv
    code = fb[:L]
    tbl = rlc if chroma else rl
    tbl[(run, lev, last)] = code
    if it % 5 == 0:
        print(f'  [{it}] +{"C" if chroma else "L"}({run},{lev},{last})={code} @MB{ok}')
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in rl.items()},
    open("data/rl_table2.json", "w"),
)
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in rlc.items()},
    open("data/rl_chroma.json", "w"),
)
print(f"final: luma {len(rl)}, chroma {len(rlc)}")
