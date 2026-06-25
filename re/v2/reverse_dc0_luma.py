"""Reverse the dc_table_index=0 intra LUMA DC differential VLC, black-box via the DivX3
(mpg4c32) encoder. Solid VxV frames deterministically encode as config (4,0,0,0) = q4,
rl=0, rl_chroma=0, dc_table=0. For a solid value V the only non-zero DC differential is
MB(0,0) block-0 with diff = V-128 (dc_scaler=8). Frames V=128+k and V=128-k differ only
in that block's sign bit, so their common prefix (from the DC field start) is the
magnitude codeword; the diverging bit is the sign. No source / no disassembly."""
import subprocess, numpy as np, os, struct, sys, json
sys.path.insert(0, ".")
import extract_div3 as EX
mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}

def c3len(bits, p):
    return 1 if bits[p] == "0" else 2

def dc_start(bits):
    # header: pictype2 q5 [5] c3(rc) c3(rt) dc1
    p = 12
    p += c3len(bits, p)
    p += c3len(bits, p)
    p += 1  # dc bit
    # mb_code (greedy)
    for L in range(1, 14):
        if bits[p:p+L] in mb_intra:
            p += L
            break
    else:
        return None
    p += 1  # acpred
    return p

W = H = 64
env = dict(os.environ, WINEDEBUG="-all", WINE_CPU_TOPOLOGY="1:0")
def enc_batch(vals):
    frames = bytearray()
    for V in vals:
        frames += np.full((H, W), V, np.uint8).tobytes()
    open("/tmp/dl.gray", "wb").write(frames)
    subprocess.run(["wine", "./vfwenc.exe", "Z:\\tmp\\dl.gray", str(W), str(H),
                    "Z:\\tmp\\dl.bin", str(len(vals)), "6000"],
                   env=env, capture_output=True, timeout=120)
    d = open("/tmp/dl.bin", "rb").read(); out = {}; p = 0
    for V in vals:
        ln = struct.unpack("<I", d[p:p+4])[0]; p += 4
        data = d[p:p+ln]; p += ln
        if ln and EX.config(data)[3] == 0:
            out[V] = "".join(format(x, "08b") for x in data)
    return out
# pos/neg pairs, batched (<=16 frames/call to avoid rate-control drift)
order = [128]
for k in range(1, 128):
    order += [128 + k, 128 - k]
order = [v for v in order if 1 <= v <= 255]
fr = {}
B = 14
for i in range(0, len(order), B):
    fr.update(enc_batch(order[i:i+B]))
print(f"got {len(fr)} dc=0 solid frames", flush=True)
table = {}  # code_bits -> diff
# k=0: V=128 diff 0
if 128 in fr:
    s = dc_start(fr[128])
    # the diff-0 code: common prefix of 128 with a neighbour, but simpler: it's the unit.
    # derive D0 later via consistency; for now pair method for k>=1.
for k in range(1, 128):
    vp, vn = 128 + k, 128 - k
    if vp not in fr or vn not in fr:
        continue
    bp, bn = fr[vp], fr[vn]
    sp, sn = dc_start(bp), dc_start(bn)
    if sp is None or sn is None or sp != sn:
        continue
    i = 0
    while sp+i < len(bp) and sn+i < len(bn) and bp[sp+i] == bn[sn+i]:
        i += 1
    mag = bp[sp:sp+i]            # magnitude codeword (common)
    signp, signn = bp[sp+i], bn[sn+i]
    table[mag + signp] = k       # V=128+k -> +k
    table[mag + signn] = -k      # V=128-k -> -k
# diff 0: from V=128, the code is what remains; get it as the prefix that isn't any signed code
if 128 in fr:
    s = dc_start(fr[128])
    # find the shortest prefix at s that, followed by the tail, is consistent: take prefix up to first known-code boundary
    b = fr[128]
    for L in range(1, 12):
        cand = b[s:s+L]
        # D0 should be prefix-free vs the magnitude+sign codes
        if not any(c != cand and c.startswith(cand) for c in table) and cand not in table:
            # tentative: D0 such that the rest tail-matches; accept shortest that makes the frame parse
            table[cand] = 0
            break
codes = list(table.keys())
coll = sum(1 for a in codes for c in codes if a != c and c.startswith(a))
print(f"derived dc=0 luma: {len(table)} codes, diff range {min(table.values())}..{max(table.values())}, prefix-collisions={coll}")
json.dump({b: d for b, d in table.items()}, open("/tmp/dc0_luma.json", "w"))
