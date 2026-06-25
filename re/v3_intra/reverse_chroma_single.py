"""Complete the chroma RL table by a reliable single-encode black-box sweep of the ffmpeg
msmpeg4 encoder. Per (run,level): encode +amp and -amp Cb single-coef frames; the code is
their common prefix (differ only at the sign bit). Early-stop a run when codes go to escape.
Clean: encoder=black box; no source used for the committed table. amp=8*level+3 hits level@q4.
"""

import subprocess, numpy as np, json, sys, os

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
ourc = {
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


def basis(u, v):
    B = np.zeros((8, 8))
    B[u, v] = 1
    return Mm.T @ B @ Mm


ZZ_AC = [
    (0, 1),
    (1, 0),
    (2, 0),
    (1, 1),
    (0, 2),
    (0, 3),
    (1, 2),
    (2, 1),
    (3, 0),
    (4, 0),
    (3, 1),
    (2, 2),
    (1, 3),
    (0, 4),
    (0, 5),
    (1, 4),
    (2, 3),
    (3, 2),
    (4, 1),
    (5, 0),
    (6, 0),
    (5, 1),
    (4, 2),
    (3, 3),
    (2, 4),
    (1, 5),
    (0, 6),
    (0, 7),
    (1, 6),
    (2, 5),
    (3, 4),
    (4, 3),
    (5, 2),
    (6, 1),
    (7, 0),
    (7, 1),
    (6, 2),
    (5, 3),
    (4, 4),
    (3, 5),
    (2, 6),
    (1, 7),
    (2, 7),
    (3, 6),
    (4, 5),
    (5, 4),
    (6, 3),
    (7, 2),
    (7, 3),
    (6, 4),
    (5, 5),
    (4, 6),
    (3, 7),
    (4, 7),
    (5, 6),
    (6, 5),
    (7, 4),
    (7, 5),
    (6, 6),
    (5, 7),
    (6, 7),
    (7, 6),
    (7, 7),
]
import extract_div3 as EX


def enc(cblk):
    Y = bytes([128] * 256)
    U = np.clip(np.round(128 + cblk), 0, 255).astype(np.uint8).tobytes()
    V = bytes([128] * 64)
    open("/tmp/qc.yuv", "wb").write(Y + U + V)
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
            "16x16",
            "-i",
            "/tmp/qc.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            "4",
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "/tmp/qc.avi",
        ],
        stderr=subprocess.DEVNULL,
    )
    fr = EX.iframes("/tmp/qc.avi", maxf=2)
    return "".join(format(x, "08b") for x in fr[0]) if fr else None


def cb_ac_start(b):
    p = 17
    m = None
    for L in range(1, 14):
        if b[p : p + L] in mb_intra:
            m = b[p : p + L]
            break
    if m is None:
        return None
    cbcr, cbpy = mb_intra[m].split("_")
    if cbcr[0] != "1":
        return None
    p += len(m) + 1
    for blk in range(4):
        if cbpy[blk] == "1":
            return None
        c = ""
        n = 0
        while n < 36:
            c += b[p + n]
            n += 1
            if c in dctab_l:
                break
        else:
            return None
        p += n
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in dctab_c:
            break
    else:
        return None
    return p + n


def code_for(run, level, last):
    u, v = ZZ_AC[run]
    amp = 8 * level + 3

    def mk(s):
        blk = s * amp * basis(u, v)
        if last == 0:
            u2, v2 = ZZ_AC[run + 1]
            blk = blk + s * 11 * basis(u2, v2)
        return blk

    bp = enc(mk(1))
    bn = enc(mk(-1))
    if bp is None or bn is None:
        return None
    ap, an = cb_ac_start(bp), cb_ac_start(bn)
    if ap is None or an is None:
        return None
    i = 0
    while ap + i < len(bp) and an + i < len(bn) and bp[ap + i] == bn[an + i]:
        i += 1
    return bp[ap : ap + i]


if __name__ == "__main__":
    # sanity on a few codes we already have
    for k in [(0, 1, 1), (0, 2, 1), (1, 1, 1)]:
        if k in ourc:
            c = code_for(*k)
            print(
                f"sanity {k}: derived='{c}' ours='{ourc[k]}' {'OK' if c==ourc[k] else 'MISMATCH'}",
                flush=True,
            )
    table = dict(ourc)
    ESC = "101101001"
    for last in (1, 0):
        for run in range(40):
            if run + 1 >= len(ZZ_AC):
                break
            for level in range(1, 40):
                c = code_for(run, level, last)
                if c is None or not c:
                    if level > 3:
                        break
                    continue
                if c.startswith(ESC):  # escape -> higher levels also escape
                    break
                table[(run, level, last)] = c
            print(f"last{last} run{run}: total {len(table)}", flush=True)
    codes = list(table.values())
    coll = sum(1 for a in codes for cc in codes if a != cc and cc.startswith(a))
    print(
        f"\nCOMPLETE chroma: {len(table)} codes (was {len(ourc)}); prefix-collisions={coll}"
    )
    json.dump(
        {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
        open("/tmp/chroma_blackbox.json", "w"),
    )
