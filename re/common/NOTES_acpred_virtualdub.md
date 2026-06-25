# acpred=1 quirk: why VirtualDub + DivX 3.11 was needed (not ffmpeg)

## Problem
The MS-MPEG4 v3 quirk (avg-DC + AC-prediction for `acpred=1` top-row blocks) only
exists in blocks with `ac_pred=1`. To reverse the quirk by controlled crafting we need
frames that have `acpred=1` AND known input content.

## The ffmpeg encoder does NOT emit acpred=1 — confirmed conclusively
`ffmpeg -c:v msmpeg4 -vtag DIV3` (with `-flags +aic`, varying qscale, mbd, and content:
gradient/noise/smooth-blobs) → acpred=1 in **0 of 24/64 MBs** in every case. The ffmpeg
encoder simply does not implement ac_pred for msmpeg4. So ffmpeg is **useless** for
crafting quirk blocks.

## Solution: the original MS encoder via VirtualDub + DivX 3.11
- VirtualDub 1.10.4 (32-bit) under **wine-11** (wow64) on Apple Silicon — works.
- Driven headlessly from Bash: `wine VirtualDub.exe /s job.vdscript /x`.
- Codec: **DivX ;-) 3.11 alpha** (`DivXc32.dll`) — a **thin wrapper** around
  **`mpg4c32.dll`** (Microsoft MPEG-4 V3). Without mpg4c32.dll the codec won't load.
- Wine registry: `HKLM\...\drivers32  vidc.DIV3 = DivXc32.dll`; both DLLs in syswow64.
- On detecting DivX3, VirtualDub shows a blocking warning ("illegal binary hacks ...
  Microsoft MPEG-4 V3") and **sometimes CRASHES** (access violation) — the codec is
  unstable. Dialogs are auto-dismissed via `osascript keystroke return`; unreliable, so
  a retry loop is needed. (A direct VFW client, `vfwenc.c`, later replaced this entirely
  — see below.)
- RESULT: controlled content → DIV3 with **acpred=1 (12/49 MBs)**. Exactly what ffmpeg
  cannot do. The breakthrough for reversing the quirk (known input → determinism).

## ⚠️ Clean-room (honest note)
This path uses **mpg4c32.dll** — precisely the binary the README called off-limits.
Usage = RUNNING it as a black box (NOT disassembling, NOT reading its code). The
resulting Go decoder contains no MS code. But the *"never touched a Microsoft binary"*
provenance no longer holds for deriving the acpred=1 quirk — a deliberate decision by
the project owner to expose a quirk the ffmpeg encoder physically cannot produce.

## Tools (re/)
- `divx_encode.py` — YUV420 → DIV3 (VirtualDub+Wine), returns the I-frame bitstream.
- `vfwenc.c` / `divx_batch.py` — direct VFW DIV3 encoder (no VirtualDub/GUI/crashes),
  batch mode (~50× faster). Build: `i686-w64-mingw32-gcc vfwenc.c -o vfwenc.exe -lvfw32 -lgdi32`.
- `extract_div3.py` — direct AVI parse: I-frames (pictype=00) + config (q,rlc,rlt,dc).
