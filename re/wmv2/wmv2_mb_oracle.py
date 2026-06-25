"""wmv2_mb_oracle.py — decoder-oracle for the 3 WMV2 P-frame mb_non_intra VLC tables
(ff_wmv2_inter_table[0/1/2]). Black-box: ffmpeg wmv2 DECODER only (pixel oracle) + the
black-box-recovered WMV2 P-header layout. No ffmpeg source / VLC tables read.

A hand-built WMV2 P-frame has one coded MB (MB0) carrying a candidate mb_type codeword; all
other MBs are skipped via parse_mb_skip (SKIP_TYPE_MPEG). Decoding tells us, from pixels,
whether MB0 is intra or inter and which of its 6 blocks are coded (cbp). Since (intra,cbp) is
fixed by the codeword alone (independent of the MV/AC bits after it), a prefix p is a complete
codeword iff classify(p+'0'+pad)==classify(p+'1'+pad); we DFS the prefix tree → all 128 leaves.

Which of the 3 tables is exercised is set by the header: cbp_table_index = map[(q>10)+(q>20)][cbp_index]
with map row0 (q<=10) = {0,2,1}. So q<=10 and cbp_index 0/1/2 select tables 0/2/1.
"""
import subprocess, os, random, json
import numpy as np

W, H = 64, 48
MBW, MBH = W // 16, H // 16
NMB = MBW * MBH
PROBE_MB = 0          # MB0: mb_x=0 avoids the top_left_mv bit
PR_R, PR_C = 0, 0
PR_RC, PR_CC = 0, 0
TMP = "/tmp/wmv2mb"
os.makedirs(TMP, exist_ok=True)


def smooth(seed, w, h, lo=40, hi=216):
    rng = random.Random(seed)
    lw, lh = w // 8 + 2, h // 8 + 2
    low = np.array([[rng.randrange(lo, hi) for _ in range(lw)] for _ in range(lh)], np.float64)
    yi = np.linspace(0, lh - 1.001, h); xi = np.linspace(0, lw - 1.001, w)
    y0 = np.floor(yi).astype(int); x0 = np.floor(xi).astype(int)
    fy = (yi - y0)[:, None]; fx = (xi - x0)[None, :]
    a = low[y0][:, x0]; b = low[y0][:, x0 + 1]; c = low[y0 + 1][:, x0]; d = low[y0 + 1][:, x0 + 1]
    return np.clip(np.round(a * (1 - fy) * (1 - fx) + b * (1 - fy) * fx + c * fy * (1 - fx) + d * fy * fx), 0, 255).astype(np.uint8)


REFY = smooth(101, W, H); REFCB = smooth(202, W // 2, H // 2); REFCR = smooth(303, W // 2, H // 2)
REFYf = REFY.astype(np.float64); REFCBf = REFCB.astype(np.float64); REFCRf = REFCR.astype(np.float64)


def build_host():
    rng = random.Random(55)
    noiseY = bytes(rng.randrange(0, 256) for _ in range(W * H))
    noiseC = bytes(rng.randrange(0, 256) for _ in range((W // 2) * (H // 2)))
    raw = REFY.tobytes() + REFCB.tobytes() + REFCR.tobytes() + noiseY + noiseC + noiseC
    open(f"{TMP}/host.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{W}x{H}", "-i", f"{TMP}/host.yuv", "-c:v", "wmv2", "-qscale:v", "4",
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    f"{TMP}/host.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/host.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/host.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    pb = data[sizes[0]:sizes[0] + sizes[1]]
    avi = bytearray(open(f"{TMP}/host.avi", "rb").read())
    return avi, bytes(avi).find(pb), len(pb)


HOST, POFF, PLEN = build_host()


def hdr_bits(cbp_index, quant=4):
    # ptype(1)=1 P | quant(5) | parse_mb_skip[skip_type=01 MPEG + NMB skip bits] |
    # cbp_index(decode012) | mspel(0) | abt[per_mb_abt=0:"1"+abt_type"0"] | per_mb_rl(0) |
    # rl(decode012=2:"11") | dc(0) | mv(1)
    skipbits = "".join("0" if n == PROBE_MB else "1" for n in range(NMB))
    cbpidx = {0: "0", 1: "10", 2: "11"}[cbp_index]
    return "1" + format(quant, "05b") + "01" + skipbits + cbpidx + "0" + "10" + "0" + "11" + "0" + "1"


def make_pframe(mbtype_bits, suffix, cbp_index):
    bits = hdr_bits(cbp_index) + mbtype_bits + suffix
    while len(bits) % 8:
        bits += "1"
    b = bytearray(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    if len(b) < PLEN:
        b += bytes(PLEN - len(b))
    return bytes(b[:PLEN])


def decode(mbtype_bits, suffix, cbp_index):
    avi = bytearray(HOST)
    avi[POFF:POFF + PLEN] = make_pframe(mbtype_bits, suffix, cbp_index)
    open(f"{TMP}/t.avi", "wb").write(avi)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/t.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    if len(out) < 2 * fsz:
        return None
    base = fsz
    y = np.frombuffer(out[base:base + W * H], np.uint8).reshape(H, W).astype(np.float64)
    cb = np.frombuffer(out[base + W * H:base + W * H + (W // 2) * (H // 2)], np.uint8).reshape(H // 2, W // 2).astype(np.float64)
    cr = np.frombuffer(out[base + W * H + (W // 2) * (H // 2):base + fsz], np.uint8).reshape(H // 2, W // 2).astype(np.float64)
    return y, cb, cr


def blk_sad(plane, ref, r0, c0, sz, ir, ic):
    rows = np.clip(r0 + ir + np.arange(sz), 0, ref.shape[0] - 1)
    cols = np.clip(c0 + ic + np.arange(sz), 0, ref.shape[1] - 1)
    return np.abs(ref[np.ix_(rows, cols)] - plane[r0:r0 + sz, c0:c0 + sz]).sum()


def classify(mbtype_bits, suffix, cbp_index):
    res = decode(mbtype_bits, suffix, cbp_index)
    if res is None:
        return None
    y, cb, cr = res
    lb = [(PR_R, PR_C), (PR_R, PR_C + 8), (PR_R + 8, PR_C), (PR_R + 8, PR_C + 8)]
    best = None
    for iy in range(-14, 15):
        for ix in range(-14, 15):
            sads = sorted(blk_sad(y, REFYf, r, c, 8, iy, ix) for (r, c) in lb)
            cost = sads[1]
            if best is None or cost < best[0]:
                best = (cost, ix, iy)
    cost, ix, iy = best
    if cost > 600.0:
        intra = 1
        coded = [1 if y[r:r + 8, c:c + 8].var() > 12.0 else 0 for (r, c) in lb]
        coded.append(1 if cb[PR_RC:PR_RC + 8, PR_CC:PR_CC + 8].var() > 12.0 else 0)
        coded.append(1 if cr[PR_RC:PR_RC + 8, PR_CC:PR_CC + 8].var() > 12.0 else 0)
    else:
        intra = 0
        coded = [1 if blk_sad(y, REFYf, r, c, 8, iy, ix) > 200.0 else 0 for (r, c) in lb]
        cix, ciy = (ix >> 1) | (ix & 1), (iy >> 1) | (iy & 1)
        coded.append(1 if blk_sad(cb, REFCBf, PR_RC, PR_CC, 8, ciy, cix) > 120.0 else 0)
        coded.append(1 if blk_sad(cr, REFCRf, PR_RC, PR_CC, 8, ciy, cix) > 120.0 else 0)
    return (intra, sum(coded[b] << (5 - b) for b in range(6)))


PAD = "10" * 40
MAXLEN = 20


def dfs(prefix, cbp_index, out):
    a = classify(prefix + "0" + PAD, "", cbp_index)
    b = classify(prefix + "1" + PAD, "", cbp_index)
    if a is not None and a == b:
        out[prefix] = a
        return
    if len(prefix) >= MAXLEN:
        return
    dfs(prefix + "0", cbp_index, out)
    dfs(prefix + "1", cbp_index, out)


if __name__ == "__main__":
    import sys
    sel = {0: 0, 2: 1, 1: 2}  # cbp_index -> table number (map row0 {0,2,1})
    targets = {0: 0, 1: 2, 2: 1}  # table -> cbp_index that selects it
    which = [int(sys.argv[1])] if len(sys.argv) > 1 else [0, 1, 2]
    for table in which:
        ci = targets[table]
        out = {}
        dfs("", ci, out)
        vals = sorted(v for v in out.values())
        ok = len(out) == 128 and len(set(out.values())) == 128
        print(f"table {table} (cbp_index={ci}): {len(out)} codewords, unique={len(set(out.values()))}, valid128={ok}")
        json.dump({k: list(v) for k, v in out.items()}, open(f"{TMP}/mb_table{table}.json", "w"))
