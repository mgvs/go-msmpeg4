"""Audit every rl-table code via decoder_oracle: decode the code alone, the lowest-
zigzag coefficient gives the TRUE (run,level). Fix entries whose stored (run,level)
disagrees — this catches contaminated entries (the real validation is the decoder's
pixel behaviour, not the stored numbers)."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

mbi = json.load(open("data/table_mb_intra_raw.json"))
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
luma_hdr = "".join(
    format(x, "08b") for x in open("/tmp/msm_craft/base.bin", "rb").read()
)[:17]
lbb = open("/tmp/msm_craft/base.bin", "rb").read()
lN = len(lbb)
lavi = bytearray(open("/tmp/msm_craft/base.avi", "rb").read())
loff = lavi.find(lbb)
chr_prefix = open("/tmp/cbase_prefix.txt").read()
cbb = open("/tmp/cbase.bin", "rb").read()
cN = len(cbb)
cavi = bytearray(open("/tmp/cbase.avi", "rb").read())
coff = cavi.find(cbb)


def coefs(ac, chroma):
    if chroma:
        bits = chr_prefix + ac
    else:
        bits = luma_hdr + mbi["00_1000"] + "0" + "10" + ac
    while len(bits) % 8:
        bits += "1"
    bb = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
    N = cN if chroma else lN
    avi = cavi if chroma else lavi
    off = coff if chroma else loff
    while len(bb) < N:
        bb.append(0)
    a = bytearray(avi)
    a[off : off + N] = bytes(bb[:N])
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
    blk = (
        np.frombuffer(o[256:320], np.uint8).reshape(8, 8)
        if chroma
        else np.frombuffer(o[:256], np.uint8).reshape(16, 16)[:8, :8]
    ).astype(float) - 128
    C = Mm @ blk @ Mm.T
    out = []
    for u in range(8):
        for v in range(8):
            if (u or v) and abs(C[u][v]) >= 2.5:
                run = next((i for i, (a, b) in enumerate(ZZ) if (a, b) == (u, v)), 99)
                out.append((run, max(1, round((abs(C[u][v]) / 4 - 1) / 2)), C[u][v]))
    return sorted(out)  # by run (lowest zigzag first)


def audit(fn, chroma):
    rl = {
        tuple(int(x) for x in k.split(",")): v for k, v in json.load(open(fn)).items()
    }
    fixed = 0
    bad = 0
    for (run, lev, last), code in list(rl.items()):
        cc = coefs(code + "0", chroma)
        if not cc:
            continue  # can't verify (high-freq) - leave
        first = cc[0]  # lowest run
        if first[0] != run or first[1] != lev:
            bad += 1
            if bad <= 15:
                print(
                    f'  {"C" if chroma else "L"} {code}: stored ({run},{lev}) but decodes ({first[0]},{first[1]})'
                )
    print(f'{"chroma" if chroma else "luma"}: {bad} mismatched entries of {len(rl)}')
    return bad


audit("data/rl_table2.json", False)
audit("data/rl_chroma.json", True)
