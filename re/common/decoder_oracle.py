"""decoder-oracle: reverse VLC codes ffmpeg's ENCODER won't emit (e.g. escape-inner,
rare run/level), using ffmpeg's DECODER as a pixel oracle — clean-room, never reads
ffmpeg source. Method: build a 1-MB DIV3 frame with a hand-placed AC code, patch it
into a real .avi (same byte length, offset = avi.find(framebytes)), decode with
ffmpeg, DCT the block -> dominant coefficient gives (run via zigzag position, level
via magnitude, sign via pos/neg). Validated: (0,1,1)=0111 -> coef at (0,1) level1."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

mbi = json.load(open("/tmp/mb_intra_real.json"))
basef = open("/tmp/msm_craft/base.bin", "rb").read()
N = len(basef)
hdr = "".join(format(x, "08b") for x in basef)[:17]
avi = open("/tmp/msm_craft/base.avi", "rb").read()
off = avi.find(basef)
M = np.array(
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
    bits = hdr + mbi["00_1000"] + "0" + "10" + ac + "1" * 30
    while len(bits) % 8:
        bits += "1"
    bb = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
    while len(bb) < N:
        bb.append(0)
    return bytes(bb[:N])


def oracle(ac):
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
        return None
    C = (
        M
        @ (np.frombuffer(o[:256], np.uint8).reshape(16, 16)[:8, :8].astype(float) - 128)
        @ M.T
    )
    mag, u, v = max(
        ((abs(C[u][v]), u, v) for u in range(8) for v in range(8) if u or v)
    )
    run = next((i for i, (a, b) in enumerate(ZZ) if (a, b) == (u, v)), -1)
    return run, round((mag / 4 - 1) / 2), round(C[u][v])


if __name__ == "__main__":
    print("calib (0,1,1)=0111:", oracle("01110"))
