"""Automated gap-fill: decode the real frame; on each AC-fail, reverse the missing
code via decoder_oracle (run=zigzag pos, level=magnitude, last=marker test, length=
search for the L that lets decode proceed), add to rl_table2, repeat until consumed."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

mbi = json.load(open("data/table_mb_intra.json"))
mb_intra = {v: k for k, v in mbi.items()}
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
basef = open("/tmp/msm_craft/base.bin", "rb").read()
N = len(basef)
hdr = "".join(format(x, "08b") for x in basef)[:17]
avi = open("/tmp/msm_craft/base.avi", "rb").read()
off = avi.find(basef)
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


def build(ac):
    bits = hdr + mbi["00_1000"] + "0" + "10" + ac
    while len(bits) % 8:
        bits += "1"
    bb = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
    while len(bb) < N:
        bb.append(0)
    return bytes(bb[:N])


def coefs(ac):
    a = bytearray(avi)
    a[off : off + N] = build(ac)
    open("/tmp/t.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/t.avi",
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
    out = []
    for u in range(8):
        for v in range(8):
            if (u or v) and abs(C[u][v]) >= 2.5:
                run = next((i for i, (a, b) in enumerate(ZZ) if (a, b) == (u, v)), -1)
                out.append((run, max(1, round((abs(C[u][v]) / 4 - 1) / 2)), C[u][v]))
    return out


def reverse_code(bits):
    # bits = the real frame's bits at the failing position (full direct code, ffmpeg reads correct length)
    c0 = coefs(bits[:40] + "0")
    if not c0:
        return None
    dom = max(c0, key=lambda x: abs(x[2]))
    c1 = coefs(bits[:40] + "0" + "0111" + "0")  # anchor
    last = 0 if len(c1) > len(c0) else 1
    run, lev = dom[0], dom[1]
    return run, lev, last


# build decoder
code2rl = {v: k for k, v in rl.items()}
maxlev = {}
for r, l, la in rl:
    maxlev[(la, r)] = max(maxlev.get((la, r), 0), l)


def match_direct(b, p):
    for L in range(1, 17):
        if b[p : p + L] in code2rl:
            return code2rl[b[p : p + L]], L
    return None, 0


def dec_tcoef(b, p):
    if b[p : p + 7] == "0000011":
        q = p + 7
        if b[q] == "1":
            q += 1
            m = "e1"
        elif b[q : q + 2] == "01":
            q += 2
            m = "e2"
        else:
            q += 2
            last = int(b[q])
            run = int(b[q + 1 : q + 7], 2)
            lv = int(b[q + 7 : q + 15], 2)
            return ((run, lv - 256 if lv >= 128 else lv, last), (q + 15) - p, None)
        rl_, L = match_direct(b, q)
        if rl_ is None:
            return (None, 0, q)  # fail at inner pos q
        run, lev, last = rl_
        q += L + 1
        if m == "e1":
            lev += maxlev.get((last, run), 0)
        return ((run, lev, last), q - p, None)
    rl_, L = match_direct(b, p)
    if rl_ is None:
        return (None, 0, p)
    run, lev, last = rl_
    return ((run, lev, last), L + 1, None)


def dcdec(b, p, tab):
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n
    return None, 0


def decode_once(b, mbw, mbh):
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
                return ("mbfail", p, my * mbw + mx)
            cbpk = mb_intra[m]
            p += len(m)
            cbcr, cbpy = cbpk.split("_")
            raw = [int(cbpy[i]) for i in range(4)] + [int(cbcr[0]), int(cbcr[1])]
            cbp = [0] * 6
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = coded[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Bb = coded[by - 1][bx] if by > 0 else 0
                Cc = coded[by][bx - 1] if bx > 0 else 0
                cbp[i] = raw[i] ^ (Cc if A == Bb else Bb)
                coded[by][bx] = cbp[i]
            cbp[4] = raw[4]
            cbp[5] = raw[5]
            p += 1
            for blk in range(6):
                tab = R.DCL if blk < 4 else R.DCC
                d, n = dcdec(b, p, tab)
                if d is None:
                    return ("dcfail", p, my * mbw + mx)
                p += n
                if cbp[blk]:
                    cnt = 0
                    while True:
                        ev, ln, failp = dec_tcoef(b, p)
                        if ev is None:
                            return ("acfail", failp, my * mbw + mx)
                        p += ln
                        cnt += 1
                        if ev[2]:
                            break
                        if cnt > 64:
                            return ("runaway", p, my * mbw + mx)
            if p >= sig:
                return ("done", p, my * mbw + mx + 1)
    return ("done", p, mbw * mbh)


b = "".join(format(x, "08b") for x in open("/tmp/divx/cfg0.bin", "rb").read())
for it in range(60):
    code2rl = {v: k for k, v in rl.items()}
    maxlev = {}
    for r, l, la in rl:
        maxlev[(la, r)] = max(maxlev.get((la, r), 0), l)
    status, p, ok = decode_once(b, 32, 18)
    if status == "done":
        print(f"★★ DONE: consumed, {ok} MBs, p={p}")
        break
    if status != "acfail":
        print(f"STOP: {status}@{p} after {ok} MBs")
        break
    failbits = b[p : p + 40]
    rv = reverse_code(failbits)
    if rv is None:
        print(f"reverse failed @{p}: {failbits[:16]}")
        break
    run, lev, last = rv
    # find code length: shortest L such that adding b[p:p+L]->(run,lev,last) lets decode proceed past p
    # determine REAL code length via oracle: smallest L where failbits[:L] gives (run,lev)
    reallen = None
    for L in range(2, 16):
        cc = coefs(failbits[:L] + "0")
        if cc:
            dom2 = max(cc, key=lambda x: abs(x[2]))
            if dom2[0] == run and dom2[1] == lev:
                reallen = L
                break
    if reallen is None:
        print(f"  no length for ({run},{lev},{last}) @{p}")
        break
    cand = failbits[:reallen]
    if cand in code2rl:
        print(f"  collision {cand} already {code2rl[cand]} vs ({run},{lev},{last})")
        break
    rl[(run, lev, last)] = cand
    code2rl[cand] = (run, lev, last)
    print(f"  +({run},{lev},{last})={cand} ({reallen}b) @MB{ok} p={p}")
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in rl.items()},
    open("data/rl_table2.json", "w"),
)
print(f"final table: {len(rl)} codes")
