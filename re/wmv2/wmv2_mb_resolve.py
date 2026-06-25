"""wmv2_mb_resolve.py — resolve the inter mb_non_intra tables from saved votes
(/tmp/wmv2_mbx/mb_inter_votes.json), breaking exact-duplicate and prefix conflicts by vote count
(the losing cbp falls back to its next-best code). No re-encoding needed."""
import json
from collections import Counter

V = json.load(open("/tmp/wmv2_mbx/mb_inter_votes.json"))

def resolve(table_votes):
    votes = {int(c): Counter(v) for c, v in table_votes.items()}
    cand = {c: votes[c].most_common(1)[0][0] for c in votes}
    cnt = {c: votes[c][cand[c]] for c in cand}
    def conflict():
        items = list(cand.items())
        for ci, a in items:
            for cj, b in items:
                if ci < cj and (a == b or b.startswith(a) or a.startswith(b)):
                    return ci, cj
        return None
    while True:
        cf = conflict()
        if not cf:
            break
        ci, cj = cf
        loser = ci if cnt[ci] <= cnt[cj] else cj
        alts = [code for code, _ in votes[loser].most_common() if code != cand[loser]]
        if alts:
            cand[loser] = alts[0]; cnt[loser] = votes[loser][alts[0]]
        else:
            del cand[loser]; del cnt[loser]
    return cand

for t in ("0", "1", "2"):
    cand = resolve(V[t])
    codes = list(cand.values())
    pf = all(not (a != b and b.startswith(a)) for a in codes for b in codes)
    bij = len(set(codes)) == len(codes)
    miss = [c for c in range(64) if c not in cand]
    print(f"table {t}: inter {len(cand)}/64 missing={miss} prefix_free={pf} bijective={bij}")
    json.dump({code: [0, cbp] for cbp, code in cand.items()}, open(f"/tmp/wmv2_mbx/mb_inter_t{t}.json", "w"))
