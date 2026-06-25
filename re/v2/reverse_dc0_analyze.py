"""Isolate dc=0 luma block-0 DC code. V=128 = 16 identical all-diff-0 MBs. Find the true
(smallest) MB period Pmb and the diff-0 luma code length |D0L| (D0L repeats 4x in an MB).
For frame V, MB(0,0) is the only non-standard MB; it ends where the standard MB Mstd
re-tiles. block-0 = MB(0,0) minus mb_code(1)+acpred(1) and the trailing D0L*3 D0C*2."""
import json
fr = {int(k): v for k, v in json.load(open("/tmp/dc0_frames.json")).items()}
mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
def c3len(b, p): return 1 if b[p] == "0" else 2
def hdr_end(b):
    p = 12; p += c3len(b, p); p += c3len(b, p); p += 1; return p
b = fr[128]; h = hdr_end(b)
# smallest MB period with >=5 repeats
Pmb = None
for cand in range(8, 30):
    reps = 1
    while b[h:h+cand] == b[h+reps*cand:h+(reps+1)*cand]: reps += 1
    if reps >= 5: Pmb = cand; break
Mstd = b[h:h+Pmb]
# |D0L|: D0L repeats 4x inside Mstd after mb(1)+acpred(1)
d0l = None
for p in range(2, Pmb // 2):
    if Mstd[2:2+p] == Mstd[2+p:2+2*p] == Mstd[2+2*p:2+3*p]:
        d0l = p; break
tail_in_mb = Pmb - 2 - d0l   # D0L*3 + D0C*2 length after block-0
print(f"Pmb={Pmb}, Mstd='{Mstd}', |D0L|={d0l}, tail_in_mb={tail_in_mb}")
table = {}; conf = 0
for V, bb in sorted(fr.items()):
    hh = hdr_end(bb)
    # find where standard tiling resumes (>= 2 consecutive Mstd) after MB(0,0)
    pos = None
    start = hh + 2
    while start < len(bb) - 2 * Pmb:
        if bb[start:start+Pmb] == Mstd and bb[start+Pmb:start+2*Pmb] == Mstd:
            pos = start; break
        start += 1
    if pos is None: continue
    mb00 = bb[hh:pos]
    code = mb00[2: len(mb00) - tail_in_mb]
    diff = V - 128
    if code in table and table[code] != diff: conf += 1
    else: table[code] = diff
codes = list(table.keys())
coll = sum(1 for a in codes for c in codes if a != c and c.startswith(a))
print(f"dc=0 luma: {len(table)} codes, diff {min(table.values())}..{max(table.values())}, collisions={coll}, conflicts={conf}")
for code, d in sorted(table.items(), key=lambda x: abs(x[1]))[:12]:
    print(f"  diff={d:4d}: {code}")
json.dump(table, open("/tmp/dc0_luma.json", "w"))
