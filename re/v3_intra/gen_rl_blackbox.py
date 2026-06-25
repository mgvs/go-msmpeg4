"""gen_rl_blackbox.py — emit tcoef_tables_extra.go (tcoefTable0/2/1VLC + maxlev arrays)
from the BLACK-BOX-derived JSON produced by rl_oracle.py's decoder-oracle DFS. No ffmpeg
source is read. maxlev[last][run] is computed from the recovered entries.
"""
import os
import json, sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tcoef_tables_extra.go")
# (json name, go var, go maxlev var, comment-label)
TABLES = [
    ("tcoefTable0VLC", "tcoefTable0VLC", "maxlevTable0", "lumaTCOEF[0]"),
    ("tcoefTable2VLC", "tcoefTable2VLC", "maxlevTable2", "lumaTCOEF[1]"),
    ("tcoefTable1VLC", "tcoefTable1VLC", "maxlevTable1", "chromaTCOEF[0]"),
]

HEADER = """package msmpeg4

// MS-MPEG4 v3 RL-VLC tables (lumaTCOEF[0], lumaTCOEF[1], chromaTCOEF[0]).
//
// Re-derived black-box: driving the ffmpeg DECODER as a pixel oracle over hand-built
// one-MB I-frames whose header selects the target RL table, reading each codeword's
// (run, level, last) from the decoded block's DCT and walking the VLC prefix tree
// (re/rl_oracle.py). No FFmpeg source or Microsoft binary was consulted.
"""


def emit():
    out = [HEADER.rstrip("\n")]
    for jname, var, mlvar, label in TABLES:
        data = json.load(open(f"/tmp/rl_oracle/{jname}.json"))
        esc = data["esc"]
        entries = [(int(r), int(l), int(la), code) for code, (r, l, la) in data["entries"].items()]
        # order: last=0 group then last=1 group, each by (run, level)
        entries.sort(key=lambda e: (e[2], e[0], e[1]))
        n_last0 = sum(1 for e in entries if e[2] == 0)
        # maxlev[last][run]
        maxlev = [[0] * 64 for _ in range(2)]
        for r, l, la, _ in entries:
            if l > maxlev[la][r]:
                maxlev[la][r] = l
        out.append("")
        out.append(f"// {var}: MS-MPEG4 v3 RL table ({label}).")
        out.append(f"// n={len(entries)}, last={n_last0}. Escape: {len(esc)} bits (0b{esc}={int(esc,2)}).")
        out.append(f"var {var} = []tcoefCode{{")
        for r, l, la, code in entries:
            out.append(f"\t{{run: {r}, level: {l}, last: {la}, length: {len(code)}, code: 0b{code}}},")
        out.append("}")
        out.append("")
        out.append(f"var {mlvar} = [2][64]int{{")
        for la in range(2):
            out.append("\t{" + ", ".join(str(x) for x in maxlev[la]) + "},")
        out.append("}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    text = emit()
    if len(sys.argv) > 1 and sys.argv[1] == "write":
        open(OUT, "w").write(text)
        print(f"wrote {OUT}")
    else:
        open("/tmp/tcoef_tables_extra.new.go", "w").write(text)
        print("wrote /tmp/tcoef_tables_extra.new.go")
