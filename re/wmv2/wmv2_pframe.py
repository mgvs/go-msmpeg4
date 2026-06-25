"""wmv2_pframe.py — probe the WMV2 P-frame header/skip structure (foundation for reversing the
3 mb_non_intra VLC tables). Black-box: ffmpeg wmv2 encoder/decoder only.

P secondary header: parse_mb_skip(skip_type 2b + skip bits) | cbp_index=decode012 |
  [mspel(1)] | [abt: per_mb_abt(1)^1, abt_type=decode012 if !per_mb_abt] | [per_mb_rl(1)] |
  rl c3 | dc(1) | mv(1). Then per-MB: mb_type(ff_wmv2_inter_table[cbp_table_index]) | ...
"""
import subprocess, os, random
import numpy as np
W, H, Q = 64, 48, 4
TMP = "/tmp/wmv2p"; os.makedirs(TMP, exist_ok=True)

def encode_clip(f0, f1):
    cw, ch = W//2, H//2; flatc = bytes([128])*(cw*ch)
    raw = f0.astype(np.uint8).tobytes()+flatc+flatc + f1.astype(np.uint8).tobytes()+flatc+flatc
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","yuv420p","-s",f"{W}x{H}",
        "-i",f"{TMP}/in.yuv","-c:v","wmv2","-qscale:v",str(Q),"-frames:v","2","-g","1000","-bf","0",
        "-sc_threshold","1000000000",f"{TMP}/c.avi"],check=True)
    sizes=[int(x) for x in subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
        "-show_entries","packet=size","-of","csv=p=0",f"{TMP}/c.avi"],capture_output=True,text=True).stdout.split()]
    data=subprocess.run(["ffmpeg","-v","error","-i",f"{TMP}/c.avi","-map","0:v:0","-c","copy","-f","data","-"],capture_output=True).stdout
    return [data[:sizes[0]], data[sizes[0]:sizes[0]+sizes[1]]]

def bits(b): return "".join(format(x,"08b") for x in b)

if __name__ == "__main__":
    rng=random.Random(7)
    f0=np.array([rng.randrange(30,220) for _ in range(W*H)],np.float64).reshape(H,W)
    # P == I -> all-skip P (smallest), exposes header + skip_type
    pk=encode_clip(f0, f0.copy())
    print(f"packets={[len(p) for p in pk]}")
    pb=bits(pk[1]); i=0
    pt=pb[i]; i+=1            # picture coding type bit (1=P)
    q=int(pb[i:i+5],2); i+=5
    skip_type=pb[i:i+2]; i+=2
    print(f"P: ptype_bit={pt} qscale={q} skip_type={skip_type} (00=NONE 01=MPEG 10=ROW 11=COL)")
    print(f"  bits after skip_type: {pb[i:i+32]}")
