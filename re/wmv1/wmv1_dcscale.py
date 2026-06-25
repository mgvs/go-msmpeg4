"""wmv1_dcscale.py — derive the WMV1 luma/chroma DC-scale tables (per qscale) black-box.

Encoder-oracle only: encode flat-gray WMV1 frames at a fixed qscale, decode, and read the
decoded DC. A flat block has DC = mean*8; the codec quantises DC_level = round(DC/dcScaler)
and reconstructs flat = DC_level*dcScaler/8, so the *step* between successive decoded values
as the input gray rises is dcScaler/8  ->  dcScaler = 8 * step. We sweep the gray to measure
the step for luma (vary Y) and chroma (vary Cb), for each qscale 1..31.
"""
import subprocess, os
import numpy as np

W, H = 16, 16
TMP = "/tmp/wmv1"
os.makedirs(TMP, exist_ok=True)


def enc_dec(Yv, Cv, q):
    y = np.full((H, W), Yv, np.uint8)
    cb = np.full((H//2, W//2), Cv, np.uint8)
    cr = np.full((H//2, W//2), 128, np.uint8)
    open(f"{TMP}/in.yuv", "wb").write(y.tobytes() + cb.tobytes() + cr.tobytes())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "wmv1", "-qscale:v", str(q),
                    "-frames:v", "1", f"{TMP}/c.avi"], check=True)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    if len(out) < W*H*3//2:
        return None, None
    y0 = float(np.frombuffer(out[:W*H], np.uint8).reshape(H, W)[0, 0])
    cb0 = float(np.frombuffer(out[W*H:W*H+(W//2)*(H//2)], np.uint8)[0])
    return y0, cb0


def step_scale(q, chroma):
    # sweep gray over a wide span; decoded values land on multiples of dcScaler/8.
    # average spacing = dcScaler/8 -> dcScaler = 8 * span / (n_unique - 1)  (fractional-resolving)
    vals = []
    for v in range(130, 251, 1):
        y0, cb0 = enc_dec(128 if chroma else v, v if chroma else 128, q)
        vals.append(cb0 if chroma else y0)
    vals = np.round(np.array(vals))
    u = np.unique(vals)
    if len(u) < 2:
        return None
    return int(round(8.0 * (u.max() - u.min()) / (len(u) - 1)))


if __name__ == "__main__":
    yt, ct = {}, {}
    for q in range(1, 32):
        yt[q] = step_scale(q, False)
        ct[q] = step_scale(q, True)
        print(f"q={q:2d}: luma dcScaler={yt[q]}  chroma dcScaler={ct[q]}")
    print("\nluma  :", [yt[q] for q in range(1, 32)])
    print("chroma:", [ct[q] for q in range(1, 32)])
