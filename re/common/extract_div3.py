"""Parse an AVI, extract I-frame (pictype=00) video chunks (00dc) as DivX3 bitstreams,
return config-0 keyframes. No ffmpeg for extraction (direct RIFF parse)."""

import struct, os


def c3(b, p):
    return (0, 1) if b[p] == "0" else ((1 if b[p + 1] == "0" else 2), 2)


def iframes(path, maxf=40):
    d = open(path, "rb").read()
    mi = d.find(b"movi")
    if mi < 0:
        return []
    p = mi + 4
    end = len(d)
    out = []
    while p + 8 <= end and len(out) < maxf:
        cid = d[p : p + 4]
        sz = struct.unpack("<I", d[p + 4 : p + 8])[0]
        data = d[p + 8 : p + 8 + sz]
        if cid[:2] == b"00" and (cid[2:] == b"dc" or cid[2:] == b"db") and sz > 4:
            b = "".join(format(x, "08b") for x in data[:8])
            if b[:2] == "00":  # I-frame
                out.append(data)
        p += 8 + sz + (sz & 1)  # padding
    return out


def config(data):
    b = "".join(format(x, "08b") for x in data[:8])
    q = int(b[2:7], 2)
    p = 12
    rc, n = c3(b, p)
    p += n
    rt, n = c3(b, p)
    p += n
    dc = b[p]
    return (q, rc, rt, int(dc))


if __name__ == "__main__":
    for fn in sorted(
        f for f in os.listdir(os.path.expanduser("~/Movies/tests")) if f.endswith(".avi")
    ):
        path = os.path.expanduser(f"~/Movies/tests/{fn}")
        ifr = iframes(path)
        if not ifr:
            continue
        cfgs = {}
        for fr in ifr:
            c = config(fr)
            cfgs.setdefault(c, 0)
            cfgs[c] += 1
        # config-0 = rlc1, rlt2, dc1 (any q)
        c0 = [c for c in cfgs if c[1] == 1 and c[2] == 2 and c[3] == 1]
        print(
            f"{fn:42} I-frames:{len(ifr)} configs:{dict(cfgs)} cfg0-keyframes:{sum(cfgs[c] for c in c0)}"
        )
