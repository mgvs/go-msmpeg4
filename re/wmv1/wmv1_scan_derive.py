"""wmv1_scan_derive.py — derive the WMV1 intra zig-zag scan via the v3-table parser.

Header discovered: MCBPC starts at bit 35 (pictype+qscale+slice + WMV1 ext-header), dc_idx=1,
rl_idx=2. With a small-amplitude single AC coefficient (level 1 -> a DIRECT RL code, no escape)
the parser reads the table run R for the coefficient at DCT position (u,v); the WMV1 intra run
offset (run_diff) is a constant, so scan index = R + OFF. We sweep all 63 AC positions, pick
OFF so the result is a clean permutation, and emit zigZagUV-style [64][2].
"""
import numpy as np
import wmv1_decode as D
import wmv1_scan as S

Sx, DCI, RLI = 35, 1, 2


def table_run(u, v, amp):
    b = S.coeff_frame(u, v, amp)
    ok, end, acs = D.parse_mb0(b, Sx, DCI, RLI)
    if ok and acs and len(acs[0]) == 1 and acs[0][0][2] == 1:
        return acs[0][0][0]   # the table run
    return None


def derive():
    positions = [(u, v) for u in range(8) for v in range(8) if (u, v) != (0, 0)]
    runs = {}
    for (u, v) in positions:
        r = None
        for amp in (14, 11, 18, 9, 22, 30):   # find an amplitude giving a clean single direct coeff
            r = table_run(u, v, amp)
            if r is not None:
                break
        runs[(u, v)] = r
    got = {p: r for p, r in runs.items() if r is not None}
    print(f"clean direct reads: {len(got)}/63")
    rs = sorted(got.values())
    print(f"distinct runs: {len(set(got.values()))}; range {min(got.values())}..{max(got.values())}")
    # scan index = run + OFF; pick OFF so positions are a permutation of 1..63
    for OFF in (1, 2, 0):
        scan = {0: (0, 0)}
        ok = True
        for p, r in got.items():
            k = r + OFF
            if k in scan:
                ok = False
                break
            scan[k] = p
        if ok and len(scan) == len(got) + 1:
            print(f"OFF={OFF}: clean, {len(scan)} indices filled")
            return scan, got
    print("no clean OFF; raw runs:")
    for p in sorted(got, key=lambda p: got[p]):
        print(f"  run {got[p]:2d} -> {p}")
    return None, got


if __name__ == "__main__":
    scan, got = derive()
    if scan:
        order = [scan.get(k) for k in range(64)]
        miss = [k for k in range(64) if order[k] is None]
        print(f"missing scan indices: {miss}")
        print("WMV1 intra zigzag (k -> u,v):")
        for k in range(64):
            print(f"  {k:2d}: {order[k]}")
