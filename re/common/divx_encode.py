"""Encode raw YUV420 -> DivX3 (MS MPEG-4 V3) via VirtualDub+Wine, return first I-frame
bitstream. Black-box use of DivX3.11/mpg4c32.dll (run, never disassembled) to obtain
acpred=1 quirk frames ffmpeg cannot emit. Codec is crash-prone -> retry loop;
osascript Return auto-dismisses the warning dialog (and 'Exit program' on a crash)."""
import os

import subprocess, os, time, signal

VDUB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "..", "tests", "VirtualDub-1.10.4", "VirtualDub.exe")


def _ret():
    subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to keystroke return'],
        capture_output=True,
    )


def encode(yuv_bytes, W, H, nframes=1, fast=False, maxtries=8):
    open("/tmp/de_in.yuv", "wb").write(yuv_bytes)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-s",
            f"{W}x{H}",
            "-i",
            "/tmp/de_in.yuv",
            "-c:v",
            "rawvideo",
            "-frames:v",
            str(nframes),
            "/tmp/de_in.avi",
        ],
        stderr=subprocess.DEVNULL,
    )
    fcc = 0x34564944 if fast else 0x33564944
    open("/tmp/de.vdscript", "w").write(
        'VirtualDub.Open("Z:\\\\tmp\\\\de_in.avi");\n'
        "VirtualDub.video.SetMode(1);\n"
        f"VirtualDub.video.SetCompression({fcc},0,10000,0);\n"
        'VirtualDub.SaveAVI("Z:\\\\tmp\\\\de_out.avi");\n'
        "VirtualDub.Close();\n"
    )
    env = dict(
        os.environ, WINEDEBUG="-all", WINE_CPU_TOPOLOGY="1:0"
    )  # 1 CPU: fewer codec races
    for t in range(maxtries):
        subprocess.run(["pkill", "-9", "-f", "VirtualDub.exe"], capture_output=True)
        try:
            os.remove("/tmp/de_out.avi")
        except FileNotFoundError:
            pass
        pr = subprocess.Popen(
            ["wine", VDUB, "/safecpu", "/s", "Z:\\tmp\\de.vdscript", "/x"],
            cwd=os.path.dirname(VDUB),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ok = False
        for _ in range(14):
            time.sleep(0.7)
            _ret()
            if (
                os.path.exists("/tmp/de_out.avi")
                and os.path.getsize("/tmp/de_out.avi") > 200
            ):
                time.sleep(0.4)  # let it finish writing
                if pr.poll() is not None or os.path.getsize("/tmp/de_out.avi") > 200:
                    ok = True
                    break
        try:
            pr.wait(timeout=3)
        except Exception:
            pr.kill()
        subprocess.run(["pkill", "-9", "-f", "VirtualDub.exe"], capture_output=True)
        if ok:
            import importlib, extract_div3 as EX

            importlib.reload(EX)
            ifr = EX.iframes("/tmp/de_out.avi", maxf=nframes + 2)
            if ifr:
                return ifr[0]
    return None


if __name__ == "__main__":
    import numpy as np, extract_div3 as EX

    s = 0
    for t in range(5):
        Y = np.fromfunction(
            lambda i, j: 90 + i * 1.0 + j * 0.7 + t * 7, (128, 128)
        ).astype(np.uint8)
        fr = encode(
            Y.tobytes() + np.full(128 * 128 // 2, 128, np.uint8).tobytes(), 128, 128
        )
        ok = fr is not None
        s += ok
        print(f"test {t}: {'OK '+str(EX.config(fr)) if ok else 'FAIL'}")
    print(f"success {s}/5")
