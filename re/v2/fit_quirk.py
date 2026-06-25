"""Fit the AC-prediction quirk from controlled crafted points (/tmp/craftpts.pkl).
Classify standard vs quirk, then test candidate prediction rules on the quirk set."""

import pickle, sys

pts = pickle.load(open("/tmp/craftpts.pkl", "rb"))


def col(d):
    return {u: d.get((u, 0), 0) for u in range(1, 8)}


def row(d):
    return {v: d.get((0, v), 0) for v in range(1, 8)}


print(f"total points: {len(pts)}")
norm = 0
quirk = []
for d in pts:
    fl = abs(d["aDC"] - d["bbDC"]) > abs(d["bbDC"] - d["cDC"])
    fr = row(d["F"])
    fc = col(d["F"])
    interior = any(k[0] > 0 and k[1] > 0 for k in d["F"])
    nocol = all(v == 0 for v in fc.values())
    norow = all(v == 0 for v in fr.values())
    if fl:
        std = (not d["F"]) or (fc == col(d["a"]) and norow and not interior)
    else:
        std = (not d["F"]) or (fr == row(d["c"]) and nocol and not interior)
    if std:
        norm += 1
    else:
        quirk.append((fl, d, interior))
print(f"STANDARD works: {norm}; QUIRK: {len(quirk)}")
qi = [q for q in quirk if not q[2]]  # no-interior quirk (clean row/col)
print(f"  quirk no-interior: {len(qi)}; with-interior: {len(quirk)-len(qi)}")
fa = [d for fl, d, i in qi if fl]
fc_ = [d for fl, d, i in qi if not fl]
print(f"  from-left:{len(fa)} from-above:{len(fc_)}")


def test(name, blocks, pred):
    if not blocks:
        return
    ok = sum(1 for d in blocks if pred(d))
    print(f"    {name}: {ok}/{len(blocks)}")


print("from-ABOVE quirk — final-row vs:")
test("c-row(top,STD)", fc_, lambda d: row(d["F"]) == row(d["c"]))
test("bb-row(topleft)", fc_, lambda d: row(d["F"]) == row(d["bb"]))
test(
    "avg(bb,c)-row",
    fc_,
    lambda d: row(d["F"])
    == {v: (row(d["bb"])[v] + row(d["c"])[v]) // 2 for v in range(1, 8)},
)
test(
    "c+bb-row",
    fc_,
    lambda d: row(d["F"]) == {v: row(d["c"])[v] + row(d["bb"])[v] for v in range(1, 8)},
)
print("from-LEFT quirk — final-col vs:")
test("a-col(left,STD)", fa, lambda d: col(d["F"]) == col(d["a"]))
test("bb-col(topleft)", fa, lambda d: col(d["F"]) == col(d["bb"]))
test(
    "avg(a,bb)-col",
    fa,
    lambda d: col(d["F"])
    == {u: (col(d["a"])[u] + col(d["bb"])[u]) // 2 for u in range(1, 8)},
)
print("\nSAMPLES from-above quirk (final-row | c-row | bb-row | a-row):")
for d in fc_[:10]:
    print(
        f"  {[row(d['F'])[v] for v in range(1,5)]} | {[row(d['c'])[v] for v in range(1,5)]} | {[row(d['bb'])[v] for v in range(1,5)]} | {[row(d['a'])[v] for v in range(1,5)]}"
    )
print("SAMPLES from-left quirk (final-col | a-col | bb-col | c-col):")
for d in fa[:10]:
    print(
        f"  {[col(d['F'])[u] for u in range(1,5)]} | {[col(d['a'])[u] for u in range(1,5)]} | {[col(d['bb'])[u] for u in range(1,5)]} | {[col(d['c'])[u] for u in range(1,5)]}"
    )
