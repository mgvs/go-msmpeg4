"""Thorough DC-quirk fit on big controlled data (/tmp/dcbig.pkl). Per (blk,fromleft):
least-squares pred ~ a*left + b*top + c*topleft reveals exact weights; plus match-rate of
named formulas. pred = oracle_DC - read_diff."""

import pickle, numpy as np

pts = pickle.load(open("/tmp/dcbig.pkl", "rb"))
print(f"{len(pts)} points")


def fl_of(d):
    return abs(d["left"] - d["topleft"]) > abs(d["top"] - d["topleft"])


for blk in (0, 1):
    for fl in (True, False):
        grp = [d for d in pts if d["blk"] == blk and fl_of(d) == fl]
        if len(grp) < 4:
            print(f"\nblk{blk} fromleft={fl}: only {len(grp)} pts, skip")
            continue
        L = np.array([d["left"] for d in grp])
        T = np.array([d["top"] for d in grp])
        TL = np.array([d["topleft"] for d in grp])
        P = np.array([d["o"] - d["diff"] for d in grp])
        # least squares pred ~ aL+bT+cTL (+intercept)
        A = np.column_stack([L, T, TL, np.ones(len(grp))])
        coef, res, *_ = np.linalg.lstsq(A, P, rcond=None)
        resid = P - A @ coef
        print(f"\n=== blk{blk} fromleft={fl} ({len(grp)} pts) ===")
        print(
            f"  pred ~ {coef[0]:.2f}*left + {coef[1]:.2f}*top + {coef[2]:.2f}*topleft + {coef[3]:.1f}  (resid std {resid.std():.2f})"
        )
        # named formulas match (tol 1)
        sel = L if fl else T
        avg = (L + TL) // 2 if fl else (T + TL) // 2

        def mr(cand):
            return int(100 * np.mean(np.abs(P - cand) <= 1))

        print(
            f"  match%%(tol1): select={mr(sel)} avg={mr(avg)} "
            f"(L+T)/2={mr((L+T)//2)} (L+TL+T)/3={mr((L+TL+T)//3)} "
            f"(3L+TL)/4={mr((3*L+TL)//4)}"
        )
