"""wmv1_interscan.py — derive the WMV1 inter scan table by P-frame decoder-oracle.

Build a WMV1 P-frame whose MB(0,0) is inter with MV=0 and cbp=block0, carrying a hand-built
ESC3 coefficient (run=R, level, last=1); the reference (I-frame) is flat, so the decoded
block-0 residual is exactly that single coefficient -> its DCT position (u,v) = inter scan
index for run R. All other MBs are skipped. VLC tables (mb_type, MV, inter RL escape) are
shared with v3; the WMV1 P-header prefix is taken from a real all-skip P-frame.
"""
import subprocess, os, random
import numpy as np

W, H = 64, 64
Q = 4
TMP = "/tmp/wmv1p"
os.makedirs(TMP, exist_ok=True)
NMB = (W // 16) * (H // 16)

PHDR = "0100100101111"           # pictype01 q4 skip1 per_mb_rl0 rl2 dc1 mv1
MBTYPE = "0001"                  # inter, cbp=32 (block0 coded)
MV0 = "00"                       # dmv=(0,0), mvVLC1
ESC_INTER = "0000011"
M = np.array([[0.5*(1/np.sqrt(2) if k == 0 else 1)*np.cos((2*n+1)*k*np.pi/16) for n in range(8)] for k in range(8)])


def esc3(run, level=3):
    return ESC_INTER + "00" + "1" + "011" + "11" + format(run, "06b") + "0" + format(level, "03b")


def build_host():
    f0 = np.full((H, W), 128, np.uint8)               # flat I -> reference
    rng = random.Random(9)
    f1 = np.array([rng.randrange(0, 256) for _ in range(W*H)], np.uint8).reshape(H, W)  # noise P -> big packet
    cw, ch = W//2, H//2
    flatc = bytes([128])*(cw*ch)
    raw = f0.tobytes()+flatc+flatc + f1.tobytes()+flatc+flatc
    open(f"{TMP}/host.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{W}x{H}", "-i", f"{TMP}/host.yuv", "-c:v", "wmv1", "-qscale:v", str(Q),
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    f"{TMP}/host.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/host.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/host.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    pb = data[sizes[0]:sizes[0]+sizes[1]]
    avi = bytearray(open(f"{TMP}/host.avi", "rb").read())
    return avi, bytes(avi).find(pb), len(pb)

HOST, POFF, PLEN = build_host()


def decode_uv(run):
    bits = PHDR + "0" + MBTYPE + MV0 + esc3(run) + "1"*(NMB-1)
    while len(bits) % 8:
        bits += "1"
    b = bytearray(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
    if len(b) < PLEN:
        b += bytes(PLEN-len(b))
    avi = bytearray(HOST)
    avi[POFF:POFF+PLEN] = bytes(b[:PLEN])
    open(f"{TMP}/t.avi", "wb").write(avi)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/t.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W*H*3//2
    if len(out) < 2*fsz:
        return None
    y1 = np.frombuffer(out[fsz:fsz+W*H], np.uint8).reshape(H, W).astype(float)  # P frame
    blk = y1[:8, :8]
    F = M @ (blk - blk.mean()) @ M.T
    bb = max(((abs(F[u, v]), u, v) for u in range(8) for v in range(8) if (u or v)))
    return (bb[1], bb[2]) if bb[0] > 2 else None


if __name__ == "__main__":
    # inter blocks have no reserved DC: ESC3 run R lands at scan[R] (offset 0).
    scan = {0: (0, 0)}   # run 0 -> position (0,0) (DC-position coeff reads flat, so seed it)
    for R in range(1, 64):
        uv = decode_uv(R)
        if uv:
            scan[R] = uv
    order = [scan.get(k) for k in range(64)]
    miss = [k for k in range(64) if order[k] is None]
    perm = len(set(v for v in order if v)) == 64 - len(miss)
    print(f"recovered {len(scan)}/64; missing={miss}; permutation={perm and not miss}")
    if not miss:
        import json
        json.dump([list(uv) for uv in order], open("/tmp/wmv1/scan_inter.json", "w"))
        print("WMV1 inter scan:")
        print("  " + ", ".join(f"{{{u},{v}}}" for u, v in order))
