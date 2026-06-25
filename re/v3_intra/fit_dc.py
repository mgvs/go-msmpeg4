"""Rich analysis of controlled ap=1 DC-quirk points (/tmp/dcpts.pkl).
predictor = oracle_DC - read_diff. Test select vs avg, find the condition, and the
b1 DC-storage signal (b1's left-neighbour = b0; offset reveals if b0 stores != reconstruction).
"""

import pickle, collections

pts = pickle.load(open("/tmp/dcpts.pkl", "rb"))
print(f"{len(pts)} ap=1 top-row DC points")


def analyze(d):
    L = d["left"]
    T = d["top"]
    TL = d["topleft"]
    o = d["o"]
    diff = d["diff"]
    pred = o - diff
    fl = abs(L - TL) > abs(T - TL)
    sel = L if fl else T
    avg = (L + TL) // 2 if fl else (T + TL) // 2
    return pred, fl, sel, avg


for blk in (0, 1):
    bp = [d for d in pts if d["blk"] == blk]
    if not bp:
        continue
    print(f"\n=== blk{blk} ({len(bp)} pts) ===")
    ns = na = nn = 0
    dec = []
    for d in bp:
        pred, fl, sel, avg = analyze(d)
        s = abs(pred - sel) <= 1
        a = abs(pred - avg) <= 1
        ns += s
        na += a
        if not s and not a:
            nn += 1
        if abs(sel - avg) > 2:
            dec.append((d, pred, fl, sel, avg, s, a))
    print(f"  SELECT:{ns} AVG:{na} neither:{nn}")
    print(
        f"  DECISIVE (|sel-avg|>2, {len(dec)}): SEL={sum(1 for x in dec if x[5])} AVG={sum(1 for x in dec if x[6])}"
    )
    print("  decisive samples: L T TL | pred | sel avg | fl | match")
    for d, pred, fl, sel, avg, s, a in dec[:14]:
        m = "SEL" if s else ("AVG" if a else "??")
        print(
            f"    {d['left']:3d} {d['top']:3d} {d['topleft']:3d} | pred={pred:3d} | sel={sel:3d} avg={avg:3d} | fl={int(fl)} | {m}"
        )
