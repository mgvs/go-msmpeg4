#!/bin/bash
# craft.sh — controlled-content encoder harness for clean-room RE.
#
# Uses ffmpeg ONLY as a black box: feed it a YUV frame whose pixels WE choose,
# get back the MS-MPEG4-v3 (DIV3) bitstream the encoder produced, and the raw
# packet bytes. By varying ONE thing at a time (a flat luma value, a gradient,
# chroma, …) and diffing the bitstreams we recover the format — no ffmpeg source
# is read, no Microsoft binary is touched.
#
# usage: craft.sh <out_dir>
# then encode helpers:  craft_solid <dir> <name> <W> <H> <Y> <Cb> <Cr> <qscale>
OUT="${1:-/tmp/msm_craft}"; mkdir -p "$OUT"

# craft_solid: a solid WxH frame, encode to DIV3, dump bitstream to <name>.bin
craft_solid() {
  local dir="$1" name="$2" W="$3" H="$4" Y="$5" Cb="$6" Cr="$7" Q="$8"
  local n=$((W*H)) c=$(((W/2)*(H/2)))
  python3 -c "import sys;sys.stdout.buffer.write(bytes([$Y]*$n+[$Cb]*$c+[$Cr]*$c))" > "$dir/$name.yuv"
  ffmpeg -y -v error -f rawvideo -pix_fmt yuv420p -s ${W}x${H} -i "$dir/$name.yuv" \
    -c:v msmpeg4 -qscale:v "$Q" -frames:v 1 -vtag DIV3 "$dir/$name.avi"
  ffmpeg -y -v error -i "$dir/$name.avi" -map 0:v:0 -c copy -frames:v 1 -f data "$dir/$name.bin"
}

# craft_yuv: encode an arbitrary pre-made YUV file (caller controls the pixels).
craft_yuv() {
  local dir="$1" name="$2" W="$3" H="$4" Q="$5" yuv="$6"
  ffmpeg -y -v error -f rawvideo -pix_fmt yuv420p -s ${W}x${H} -i "$yuv" \
    -c:v msmpeg4 -qscale:v "$Q" -frames:v 1 -vtag DIV3 "$dir/$name.avi"
  ffmpeg -y -v error -i "$dir/$name.avi" -map 0:v:0 -c copy -frames:v 1 -f data "$dir/$name.bin"
}

# bits: print a .bin as an MSB-first bit string.
bits() { python3 -c "b=open('$1','rb').read();print(''.join(format(x,'08b') for x in b))"; }

# decode: ffmpeg as pixel oracle — first luma byte of the decoded frame.
oracle_y0() { python3 -c "import subprocess;print(subprocess.run(['ffmpeg','-v','error','-i','$1','-f','rawvideo','-pix_fmt','yuv420p','-'],capture_output=True).stdout[0])"; }

"$@"
