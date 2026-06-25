"""wmv2_probe.py — probe the WMV2 (WMV8) I-frame header structure, black-box.
WMV2 I-header: pict_type(1)=0 | I7(7) | qscale(5) | j_type(1) | [per_mb_rl(1)] |
  [rl_chroma c3 + rl c3 if !per_mb_rl] | dc(1).  Ext flags (mspel/abt/j_type/per_mb_rl) live in
the 32-bit codec extradata: fps5|bitrate11|mspel1|loop1|abt1|jtype1|tlmv1|permbrl1|code3.
ffmpeg's encoder fixes mspel=abt=jtype=permbrl=1, j_type(frame)=0.
"""
import subprocess, os, random
import numpy as np
W,H,Q=64,48,4
TMP="/tmp/wmv2"; os.makedirs(TMP,exist_ok=True)

def encode(frame):
    cw,ch=W//2,H//2; flatc=bytes([128])*(cw*ch)
    raw=frame.astype(np.uint8).tobytes()+flatc+flatc
    open(f"{TMP}/in.yuv","wb").write(raw)
    subprocess.run(["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","yuv420p","-s",f"{W}x{H}",
        "-i",f"{TMP}/in.yuv","-c:v","wmv2","-qscale:v",str(Q),"-frames:v","1","-g","1",
        f"{TMP}/c.avi"],check=True)
    # extract video packet + extradata
    data=subprocess.run(["ffmpeg","-v","error","-i",f"{TMP}/c.avi","-map","0:v:0","-c","copy","-f","data","-"],
        capture_output=True).stdout
    # extradata via ffprobe (hex)
    ex=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=extradata",
        "-of","default=nk=1:nw=1",f"{TMP}/c.avi"],capture_output=True,text=True).stdout
    return data, ex

def bits(b): return "".join(format(x,"08b") for x in b)

if __name__=="__main__":
    rng=random.Random(7)
    f=np.array([rng.randrange(30,220) for _ in range(W*H)],np.float64).reshape(H,W)
    data,ex=encode(f)
    pb=bits(data)
    print(f"I-frame {len(data)}B; extradata: {ex.strip()[:40]}")
    i=0
    pt=pb[i]; i+=1
    i7=pb[i:i+7]; i+=7
    q=int(pb[i:i+5],2); i+=5
    jt=pb[i]; i+=1
    print(f"pict_type={pt} I7={i7}(0x{int(i7,2):02X}) qscale={q} j_type={jt}")
    print(f"  next bits (per_mb_rl + rl_c c3 + rl c3 + dc + MB): {pb[i:i+20]}")
