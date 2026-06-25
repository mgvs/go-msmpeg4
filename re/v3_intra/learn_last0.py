import subprocess, numpy as np, json, glob, os

exec(
    open("/tmp/learn7.py").read().split("# 3-coef")[0]
)  # reuse all funcs incl consume/decblock/learnpass
# generate targeted frames: consecutive triples, vary middle amp -> run0 last0 levels
new = []


def enc2(nm, B):
    if not os.path.exists(f"/tmp/msm_craft/{nm}.bin"):
        enc(nm, B)
    new.append(nm)


import itertools

mids = [12, 14, 16, 18, 22, 26, 32, 40, 50, 62]
for k in range(2, 12):  # first at k, middle k+1 (run0), third k+3
    for am in mids:
        u1, v1 = ZIG[k]
        u2, v2 = ZIG[k + 1]
        u3, v3 = ZIG[k + 3]
        enc2(
            f"P_{k}_{am}", 24 * basis(u1, v1) + am * basis(u2, v2) + 24 * basis(u3, v3)
        )
# also more 2-coef to fill higher runs/levels
for p1 in range(1, 10):
    for p2 in range(p1 + 1, 16):
        enc2(f"Q_{p1}_{p2}", 40 * basis(*ZIG[p1]) + 30 * basis(*ZIG[p2]))
allf = [
    (os.path.basename(f)[:-4], oseq(os.path.basename(f)[:-4]))
    for f in glob.glob("/tmp/msm_craft/L_*.bin")
]
allf += [
    (os.path.basename(f)[:-4], oseq(os.path.basename(f)[:-4]))
    for f in glob.glob("/tmp/msm_craft/C_*.bin")
]
allf += [(nm, oseq(nm)) for nm in new]
print(f"frames: {len(allf)}")
for it in range(20):
    n = learnpass(allf)
    print(f"pass {it}: +{n} total {len(learned)}")
    if n == 0:
        break
byrun = {}
for (r, l, la), m in learned.items():
    byrun.setdefault(r, {})[l] = m
print("last=0 codes:")
for r in sorted(byrun):
    print(f"  run{r}: " + " ".join(f"L{l}={byrun[r][l]}" for l in sorted(byrun[r])))
json.dump(
    {f"{k[0]},{k[1]},{k[2]}": v for k, v in learned.items()},
    open("/tmp/learned_last0.json", "w"),
)
print(f"saved {len(learned)} last=0 codes")
