"""Iterative gap-fill: decode real frame, on AC-fail reverse the missing code via
decoder_oracle (run from zigzag pos, level from magnitude, last via marker test), add.
"""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

mbi = json.load(open("data/table_mb_intra.json"))
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
            if (u or v) and abs(C[u][v]) >= 6:
                run = next((i for i, (a, b) in enumerate(ZZ) if (a, b) == (u, v)), -1)
                out.append(
                    (run, round((abs(C[u][v]) / 4 - 1) / 2), round(C[u][v]), u, v)
                )
    return sorted(out)


# reverse 00011010
code = "00011010"
c0 = coefs(code + "0")  # code + sign0
c1 = coefs(code + "0" + "0111" + "0")  # + (0,1,1) anchor
print(f"code {code}+sign0 -> coefs {[(r,l) for r,l,val,u,v in c0]}")
print(f"  + anchor(0,1,1) -> coefs {[(r,l) for r,l,val,u,v in c1]}")
# if anchor adds a coef -> code was last0; else last1
last = 0 if len(c1) > len(c0) else 1
dom = max(c0, key=lambda x: abs(x[2])) if c0 else None
print(f"  => {code} = (run={dom[0]},level={dom[1]},last={last})")
