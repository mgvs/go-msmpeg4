"""v2_pframe.py — probe the MS-MPEG4 v2 P-frame structure to reverse the v2 MB-type VLC
(8 entries) and the H.263 MV VLC. Black-box: ffmpeg msmpeg4v2 encoder/decoder only.

v2 P-frame header: pictype(2)=01 | qscale(5) | use_skip_mb_code(1). rl=2,dc=0,mv=0 (fixed).
Per-MB (P): [skip(1)] | v2_mb_type (code 0..7 -> intra=code>>2, cbp_chroma=code&3) |
  if inter: cbpy(H.263) + cbp-invert + MV(2x: ff_h263_mv_vlc code + sign + f_code shift).
"""
import subprocess, os, random
import numpy as np

W, H = 64, 64
Q = 4
TMP = "/tmp/v2p"
os.makedirs(TMP, exist_ok=True)


def encode_clip(f0, f1):
    cw, ch = W // 2, H // 2
    flatc = bytes([128]) * (cw * ch)
    raw = f0.astype(np.uint8).tobytes()+flatc+flatc + f1.astype(np.uint8).tobytes()+flatc+flatc
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "msmpeg4v2", "-qscale:v", str(Q),
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-vtag", "MP42", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    pk, off = [], 0
    for s in sizes:
        pk.append(data[off:off+s]); off += s
    return pk


def bits(b):
    return "".join(format(x, "08b") for x in b)


if __name__ == "__main__":
    rng = random.Random(7)
    f0 = np.array([rng.randrange(30, 220) for _ in range(W*H)], np.float64).reshape(H, W)
    # all-skip P (P==I) -> exposes header
    pk = encode_clip(f0, f0.copy())
    print(f"packets={[len(p) for p in pk]}")
    pb = bits(pk[1])
    i = 0
    pictype = pb[i:i+2]; i += 2
    q = int(pb[i:i+5], 2); i += 5
    skip = pb[i]; i += 1
    print(f"P header: pictype={pictype} q={q} use_skip={skip} ; bits[i:i+24]={pb[i:i+24]}")
    # global-shift P (uniform motion) -> first non-skip MB reveals mb_type + cbpy + MV
    sh = np.empty_like(f0)
    sh[:, 2:] = f0[:, :-2]; sh[:, :2] = f0[:, :1]
    pk2 = encode_clip(f0, sh)
    pb2 = bits(pk2[1])
    print(f"shift(2,0) P {len(pk2[1])}B: bits[8:48]={pb2[8:48]}")

# --- v2 P-frame structure findings (2026-06-25) ---
# Header (P): pictype(2)=01 | qscale(5) | use_skip_mb_code(1)  [rl=2,dc=0,mv=0 fixed] = 8 bits.
# Per MB (P): [skip(1)] | v2_mb_type(code 0..7: intra=code>>2, cbp_chroma=code&3) |
#   inter: cbpy(ff_h263_cbpy) ; cbp|=cbpy<<2 ; if (cbp&3)!=3: cbp^=0x3C ;
#          MVx,MVy = msmpeg4v2_decode_motion (ff_h263_mv_vlc code + sign + f_code shift),
#          H.263 median MV prediction (ff_h263_pred_motion).
#   intra: ac_pred(1) | cbpy | per-block (v2 DC + AC), like the v2 I-frame.
# Global-shift P -> only MB0 coded (predictor 0); inter cbp0 prefix observed ~ "1"+cbpy(15)="11".
# H.263 MVD codes (after prefix, |MVD|=2*|dx| half-pel): val2~"001", val4~"000011", val6~"000010".
#
# DONE (2026-06-25): DecodePFrameV2 implemented and verified at 63-74 dB (luma+chroma, q1..16,
# inter + intra MBs) vs the reference decoder.
#   - H.263 MVD VLC: open ITU-T H.263 Table 14 (cross-checked black-box: val2/4/6 above).
#   - v2_mb_type (8 codes): "1"=inter cbp0; "00","011","01001"=inter cbp1..3;
#     "0101","0100001","0100000","010001"=intra cbp0..3. Every code verified bit-exact by
#     full-frame PSNR (a wrong code desyncs the MB layer -> low PSNR).
#   - chroma MV = (mv>>1)|(mv&1); MV wraps to +-64; H.263 median MV prediction.
