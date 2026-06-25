"""Reverse dc_table_index=0 intra LUMA DC VLC, black-box via DivX3. Solid VxV frames at
quality<=8000 encode as config (4,0,0,0) (q4, dc_table=0); diff = V-128 (dc_scaler 8).
Tail-anchor isolation: in an all-flat frame the luma diff-0 code D0L repeats; the block-0
DC code is the prefix before the fixed tail TAIL2 = D0L D0L D0C D0C [other MBs]. No source."""
import subprocess, numpy as np, os, struct, sys, json
sys.path.insert(0, ".")
import extract_div3 as EX
mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}

def c3len(b, p): return 1 if b[p] == "0" else 2
def dc_start(b):
    p = 12; p += c3len(b, p); p += c3len(b, p); p += 1
    for L in range(1, 14):
        if b[p:p+L] in mb_intra: return p + L + 1
    return None

W = H = 64
env = dict(os.environ, WINEDEBUG="-all", WINE_CPU_TOPOLOGY="1:0")
def enc_batch(vals):
    fr = bytearray()
    for V in vals: fr += np.full((H, W), V, np.uint8).tobytes()
    open("/tmp/dl.gray", "wb").write(fr)
    subprocess.run(["wine", "./vfwenc.exe", "Z:\\tmp\\dl.gray", str(W), str(H),
                    "Z:\\tmp\\dl.bin", str(len(vals)), "6000"], env=env, capture_output=True, timeout=120)
    d = open("/tmp/dl.bin", "rb").read(); out = {}; p = 0
    for V in vals:
        ln = struct.unpack("<I", d[p:p+4])[0]; p += 4
        data = d[p:p+ln]; p += ln
        if ln and EX.config(data)[3] == 0 and EX.config(data)[0] == 4:
            out[V] = "".join(format(x, "08b") for x in data)
    return out

fr = {}
for off in range(16):  # diverse stride-16 batches -> codec stays at (4,0,0,0)
    fr.update(enc_batch(list(range(1 + off, 256, 16))))
json.dump(fr, open("/tmp/dc0_frames.json","w"))
print(f"got {len(fr)} (4,0,0,0) frames", flush=True)

# find |D0L| from V=128 (all-diff0): smallest period with >=3 repeats after dc_start
b128 = fr[128]; s = dc_start(b128)
d0l_len = None
for p in range(2, 20):
    if b128[s:s+p] == b128[s+p:s+2*p] == b128[s+2*p:s+3*p] == b128[s+3*p:s+4*p]:
        d0l_len = p; break
D0L = b128[s:s+d0l_len]
TAIL2 = b128[s+d0l_len: s+d0l_len+120]  # meaningful chunk of the fixed tail
print(f"D0L='{D0L}' (len {d0l_len}); TAIL2 head='{TAIL2[:24]}...'", flush=True)

table = {D0L: 0}
for V, b in fr.items():
    s = dc_start(b)
    if s is None: continue
    # block0 = prefix before TAIL2 reappears
    L = None
    for cand in range(1, 32):
        if b[s+cand: s+cand+len(TAIL2)] == TAIL2:
            L = cand; break
    if L is None: continue
    code = b[s:s+L]
    diff = V - 128
    if code in table and table[code] != diff:
        pass  # conflict; keep first
    else:
        table[code] = diff
codes = list(table.keys())
coll = sum(1 for a in codes for c in codes if a != c and c.startswith(a))
print(f"dc=0 luma: {len(table)} codes, diff {min(table.values())}..{max(table.values())}, collisions={coll}")
json.dump(table, open("/tmp/dc0_luma.json", "w"))
