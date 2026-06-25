"""In-place acpred=1 probe: cfg0 MB0-34 are perfect, so the real neighbours of
MB(3,1)blk0 are set up correctly. Replace blk0's DC+AC bits (at bit 5363) with a
controlled code, decode via the ffmpeg oracle (skeleton-patched), and read back
blk0's pixels at (16,48) -> reverse how acpred=1 codes the DC and AC."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

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
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
skf = open("/tmp/sk_frame.bin", "rb").read()
skavi = bytearray(open("/tmp/sk.avi", "rb").read())
skoff = skavi.find(skf)
cfg = "".join(format(x, "08b") for x in open("/tmp/divx/cfg0.bin", "rb").read())
B0 = 5363  # bit offset of MB(3,1)blk0 DC (verified: after mb`1`+acpred`1`)


def decode_block(blk0bits):
    """prefix(up to B0) + controlled blk0 + original tail, byte-padded, decode, read (16,48)."""
    bits = cfg[:B0] + blk0bits + cfg[B0:]
    bits = bits[: len(cfg)]  # keep original bit length (frame byte size)
    by = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits) // 8 * 8, 8))
    fb = bytes(by)
    a = bytearray(skavi)
    a[skoff : skoff + len(skf)] = fb + skf[len(fb) :]
    open("/tmp/pr.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/pr.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    if len(o) < 512 * 288:
        return None
    Yt = np.frombuffer(o[: 512 * 288], np.uint8).reshape(288, 512).astype(float)
    C = Mm @ Yt[16:24, 48:56] @ Mm.T
    return C


def levels(C, q=4):
    out = {}
    for u in range(8):
        for v in range(8):
            F = C[u][v]
            if abs(F) >= 2:
                out[(u, v)] = round(F, 1)
    return out


# Probe 1: DC=`10`(diff0) + NO ac (need last immediately). Minimal: just see DC.
# rl (0,1,1)=0111 last1. Use DC`10` + (0,1,1) code `0111`+sign0 to terminate.
print("=== Probe: DC=10(diff0) + single AC (0,1,1) pos ===")
C = decode_block("10" + "0111" + "0")  # DC + (0,1,1)+sign0, last=1 ends block
if C is not None:
    print("  blk0 coefs:", levels(C))
print("=== Probe: DC=10 + single AC (0,1,1) NEG ===")
C = decode_block("10" + "0111" + "1")
if C is not None:
    print("  blk0 coefs:", levels(C))
print("=== Probe: DC=10 + (2,1,1) `01110`? use (3,1,1)=0010001 ===")
C = decode_block("10" + "0010001" + "0")
if C is not None:
    print("  blk0 coefs:", levels(C))
