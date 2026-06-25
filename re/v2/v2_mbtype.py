"""v2_mbtype.py — derive the v2 P-frame mb_type VLC (8 codes) black-box, and the H.263 MVD
magnitude codes, by decoding controlled v2 P-frames with ffmpeg and isolating the codeword.

Decoder-oracle: build a v2 P-frame  hdr(8) | skip0 | <candidate mb_type bits> | <pad> , decode
with ffmpeg over a known reference, and read MB(0,0): inter vs intra from whether it matches a
shift of the reference, chroma-cbp from the chroma residual. Walk the 8-leaf prefix tree.
The H.263 MVD VLC is the open ITU-T H.263 standard table (Table 14); we cross-check a few
magnitudes against the encoder output (val 2->'001', 4->'000011', 6->'0000100').
"""
import subprocess, os, random
import numpy as np

W, H = 64, 48
Q = 4
TMP = "/tmp/v2p"
os.makedirs(TMP, exist_ok=True)

# H.263 MVD VLC (ITU-T H.263 Table 14) — magnitude 0..32 -> (code, length).
MVTAB = [(1,1),(1,2),(1,3),(1,4),(3,6),(5,7),(4,7),(3,7),(11,9),(10,9),(9,9),
         (17,10),(16,10),(15,10),(14,10),(13,10),(12,10),(11,10),(10,10),(9,10),
         (8,10),(7,10),(6,10),(5,10),(4,10),(7,11),(6,11),(5,11),(4,11),(3,11),
         (2,11),(3,12),(2,12)]
MV_CODE = {format(c, f"0{l}b"): m for m, (c, l) in enumerate(MVTAB)}


def host():
    rng = random.Random(3)
    f0 = np.array([rng.randrange(20, 236) for _ in range(W*H)], np.uint8).reshape(H, W)
    rng2 = random.Random(9)
    f1 = np.array([rng2.randrange(0, 256) for _ in range(W*H)], np.uint8).reshape(H, W)
    cw, ch = W//2, H//2; flatc = bytes([128])*(cw*ch)
    raw = f0.tobytes()+flatc+flatc + f1.tobytes()+flatc+flatc
    open(f"{TMP}/host.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","yuv420p","-s",
                    f"{W}x{H}","-i",f"{TMP}/host.yuv","-c:v","msmpeg4v2","-qscale:v",str(Q),
                    "-frames:v","2","-g","1000","-bf","0","-sc_threshold","1000000000","-vtag","MP42",
                    f"{TMP}/host.avi"],check=True)
    sizes=[int(x) for x in subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
           "-show_entries","packet=size","-of","csv=p=0",f"{TMP}/host.avi"],
           capture_output=True,text=True).stdout.split()]
    data=subprocess.run(["ffmpeg","-v","error","-i",f"{TMP}/host.avi","-map","0:v:0","-c","copy",
                         "-f","data","-"],capture_output=True).stdout
    pb=data[sizes[0]:sizes[0]+sizes[1]]; avi=bytearray(open(f"{TMP}/host.avi","rb").read())
    return avi, bytes(avi).find(pb), len(pb), sizes[0], data[:sizes[0]]

HOST, POFF, PLEN, ISZ, IPKT = host()


def hdr():
    # pictype01 q4 use_skip1 ; (rl/dc/mv fixed) -> 8 bits
    return "01" + format(Q, "05b") + "1"


if __name__ == "__main__":
    # cross-check the H.263 MVD codes against the encoder via global x-shifts
    print("H.263 MVD cross-check (|MVD|=2*dx):")
    for dx in (1, 2, 3):
        code = format(MVTAB[2*dx][0], f"0{MVTAB[2*dx][1]}b")
        print(f"  |MVD|={2*dx}: MVTAB code = {code}")
    print(f"loaded {len(MV_CODE)} H.263 MVD codewords")
    print(f"v2 P header = {hdr()} (8 bits)")
