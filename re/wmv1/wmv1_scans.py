"""wmv1_scans.py — derive all WMV1 intra scan tables (zigzag, alt-vertical, alt-horizontal)
by decoder-oracle (hand-built ESC3 places one coefficient at scan index R+1; read (u,v) from
the decoded block). Saves /tmp/wmv1/scan_{intra,altv,alth}.json.

  intra zigzag : block0, ac_pred=0           (no neighbours -> plain scan)
  alt-vertical : block0, ac_pred=1, dir=0     (no neighbours -> alt-V scan, no AC pred added)
  alt-horizontal: block2(bottom-left), ac_pred=1, dir=1 (block0 has a nonzero DC so block2's
                  top-gradient forces dir=1; block0 uncoded -> predicted AC row = 0)
"""
import subprocess, json
import numpy as np
import wmv1_scan_oracle as O   # HOST/POFF/PLEN, esc3, M, PREFIX, DC1_L/DC1_C

HDR = O.PREFIX[:35]                          # constant WMV1 header (MCBPC starts at 35)
MCBPC_B0 = "010101"                          # raw 00_1110 -> actual block0 only
MCBPC_B2 = "000010"                          # raw 00_0011 -> actual block2 only
M = O.M


def decode(frame_bits, block_origin):
    bits = frame_bits
    while len(bits) % 8:
        bits += "0"
    b = bytearray(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
    if len(b) < O.PLEN:
        b += bytes(O.PLEN - len(b))
    avi = bytearray(O.HOST)
    avi[O.POFF:O.POFF+O.PLEN] = bytes(b[:O.PLEN])
    open("/tmp/wmv1/t.avi", "wb").write(avi)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", "/tmp/wmv1/t.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    if len(out) < 256:
        return None
    y = np.frombuffer(out[:256], np.uint8).reshape(16, 16).astype(float)
    r0, c0 = block_origin
    blk = y[r0:r0+8, c0:c0+8]
    F = M @ (blk - blk.mean()) @ M.T
    bb = max(((abs(F[u, v]), u, v) for u in range(8) for v in range(8) if (u or v)))
    return (bb[1], bb[2]) if bb[0] > 2 else None


def frame_b0(run, ac_pred):
    return HDR + MCBPC_B0 + str(ac_pred) + O.DC1_L + O.esc3(run) + O.DC1_L*3 + O.DC1_C*2


def frame_b2(run):
    # ac_pred=1; block0 uncoded with nonzero DC (mag5,+ = '000010'); block2 coded
    return (HDR + MCBPC_B2 + "1" + "000010" + O.DC1_L + O.DC1_L + O.esc3(run)
            + O.DC1_L + O.DC1_C*2)


def sweep(framefn, origin):
    scan = {0: (0, 0)}
    for R in range(63):
        uv = decode(framefn(R), origin)
        if uv:
            scan[R+1] = uv
    vals = list(scan.values())
    return scan, (len(set(vals)) == len(vals) == 64)


if __name__ == "__main__":
    res = {}
    intra, ok1 = sweep(lambda R: frame_b0(R, 0), (0, 0))
    altv, ok2 = sweep(lambda R: frame_b0(R, 1), (0, 0))
    alth, ok3 = sweep(frame_b2, (8, 0))
    for name, scan, ok in (("intra", intra, ok1), ("altv", altv, ok2), ("alth", alth, ok3)):
        order = [list(scan[k]) for k in range(64)]
        json.dump(order, open(f"/tmp/wmv1/scan_{name}.json", "w"))
        print(f"{name}: permutation={ok}")
        print("  " + ", ".join(f"{{{u},{v}}}" for u, v in order))
