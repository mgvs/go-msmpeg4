import sys, json

sys.path.insert(0, "re")
import recon_loop as R

mb_intra = {v: k for k, v in json.load(open("/tmp/mb_intra_real.json")).items()}
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("/tmp/rl_full2.json")).items()
}
code2rl = {v: k for k, v in rl.items()}  # code -> (run,lev,last)
maxlev = {}
for run, lev, last in rl:
    maxlev[(last, run)] = max(maxlev.get((last, run), 0), lev)


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
            rl_, L = match_direct(b, q)
            if rl_ is None:
                return None
            run, lev, last = rl_
            q += L
            sign = b[q]
            q += 1
            lev += maxlev.get((last, run), 0)
            return (run, -lev if sign == "1" else lev, last), q - p
        elif b[q : q + 2] == "01":
            q += 2
            rl_, L = match_direct(b, q)
            if rl_ is None:
                return None
            run, lev, last = rl_
            q += L
            sign = b[q]
            q += 1
            return (run, -lev if sign == "1" else lev, last), q - p
        else:
            q += 2
            last = int(b[q])
            run = int(b[q + 1 : q + 7], 2)
            lev = int(b[q + 7 : q + 15], 2)
            if lev >= 128:
                lev -= 256
            return (run, lev, last), (q + 15) - p
    rl_, L = match_direct(b, p)
    if rl_ is None:
        return None
    run, lev, last = rl_
    sign = b[p + L]
    return (run, -lev if sign == "1" else lev, last), L + 1


def dcdec(b, p, tab):
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n
    return None, 0


def decode(binf, mbw, mbh, hdr=17):
    b = "".join(format(x, "08b") for x in open(binf, "rb").read())
    sig = R.sig_end(b)
    p = hdr
    coded = [[0] * (2 * mbw) for _ in range(2 * mbh)]
    for my in range(mbh):
        for mx in range(mbw):
            m = None
            for L in range(1, 14):
                if b[p : p + L] in mb_intra:
                    m = b[p : p + L]
                    break
            if m is None:
                return f"MB({mx},{my})@{p} code-unk (ok {my*mbw+mx})"
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
                C = coded[by][bx - 1] if bx > 0 else 0
                cbp[i] = raw[i] ^ (C if A == Bb else Bb)
                coded[by][bx] = cbp[i]
            cbp[4] = raw[4]
            cbp[5] = raw[5]
            p += 1
            for blk in range(6):
                tab = R.DCL if blk < 4 else R.DCC
                d, n = dcdec(b, p, tab)
                if d is None:
                    return f"MB({mx},{my})blk{blk} DCfail@{p} (ok {my*mbw+mx})"
                p += n
                if cbp[blk]:
                    cnt = 0
                    while True:
                        dt = dec_tcoef(b, p)
                        if dt is None:
                            return f"MB({mx},{my})blk{blk} ACfail@{p}:{b[p:p+20]} (ok {my*mbw+mx})"
                        p += dt[1]
                        cnt += 1
                        if dt[0][2]:
                            break
                        if cnt > 64:
                            return f"runaway MB({mx},{my})"
            if p >= sig:
                return f"reached sig@MB({mx},{my}) ok={my*mbw+mx+1}/{mbw*mbh}"
    return f"★ ALL {mbw*mbh} MBs CONSUMED! p={p} sig={sig}"


print(decode(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
