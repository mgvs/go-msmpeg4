"""gen_mv_blackbox.py — emit pframe_mv_vlc.go from the BLACK-BOX-derived JSON
(/tmp/pf_oracle/mv{0,1}_blackbox.json, produced by pframe_oracle.py's decoder-oracle
tree walk). No ffmpeg source is read. Escape leaf -> sentinel {-32,-32}.

Run with `diff` to compare against the current file without writing.
"""
import os
import json, sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pframe_mv_vlc.go")

HEADER = """package msmpeg4

// mvVLC0 and mvVLC1: MS-MPEG4 v3 combined motion-vector VLC tables.
//
// Re-derived black-box: each codeword and its (dmvx,dmvy) value was recovered by
// driving the ffmpeg DECODER as a pixel oracle over hand-built P-frames and reading
// back the motion of one probed macroblock (re/pframe_oracle.py walks the complete
// VLC prefix tree). No ffmpeg source or Microsoft binary was consulted.
//
// The sentinel {-32,-32} marks the escape leaf: two u(6) raw fields follow and
// dmv = raw - 32.
"""


def emit():
    lines = [HEADER.rstrip("\n"), ""]
    for idx in (0, 1):
        data = json.load(open(f"/tmp/pf_oracle/mv{idx}_blackbox.json"))
        entries = []
        for code, v in data.items():
            if v == "ESC":
                entries.append((code, -32, -32))
            else:
                entries.append((code, v[0], v[1]))
        entries.sort(key=lambda e: (len(e[0]), e[0]))
        lines.append(f"var mvVLC{idx} = func() map[string][2]int {{")
        lines.append("\traw := map[string][2]int{")
        for code, a, b in entries:
            lines.append(f'\t\t"{code}": {{{a}, {b}}},')
        lines.append("\t}")
        lines.append("\treturn raw")
        lines.append("}()")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = emit()
    if len(sys.argv) > 1 and sys.argv[1] == "write":
        open(OUT, "w").write(text)
        print(f"wrote {OUT}")
    else:
        open("/tmp/pf_oracle/pframe_mv_vlc.new.go", "w").write(text)
        print("wrote /tmp/pf_oracle/pframe_mv_vlc.new.go (diff mode)")
