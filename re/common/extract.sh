#!/bin/bash
# Clean-room RE harness: ffmpeg ONLY as a black box (bytes in / pixels out).
# Never reads ffmpeg source or MS binaries.
OUT="${1:-/tmp/msm_re}"; mkdir -p "$OUT"
SAMPLES="
v3_clip|$HOME/Movies/movie6.avi
v3_aggr|$HOME/Movies/movie10.avi
v3_vaaa|$HOME/Movies/movie8.avi
v3_clan|$HOME/Movies/movie5.avi
v2_stat|$HOME/Movies/movie11.avi
"
echo "$SAMPLES" | while IFS='|' read -r k f; do
  [ -n "$k" ] || continue
  [ -f "$f" ] || { echo "$k: missing $f"; continue; }
  ffmpeg -y -v error -i "$f" -map 0:v:0 -c copy -frames:v 1 -f data "$OUT/$k.f1.bin"
  ffmpeg -y -v error -i "$f" -map 0:v:0 -frames:v 1 -f rawvideo -pix_fmt yuv420p "$OUT/$k.f1.yuv"
  wh=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$f")
  printf "%-9s %s  f1=%s B  yuv=%s B\n" "$k" "$wh" "$(stat -f%z "$OUT/$k.f1.bin" 2>/dev/null)" "$(stat -f%z "$OUT/$k.f1.yuv" 2>/dev/null)"
done
