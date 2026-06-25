package msmpeg4

import (
	"fmt"
	"image"
	"os"
	"os/exec"
	"testing"
)

func encodeWMV2(t *testing.T, w, h, q int, yuv []byte) (pkt, ref []byte) {
	t.Helper()
	dir := t.TempDir()
	in, avi := dir+"/in.yuv", dir+"/c.avi"
	os.WriteFile(in, yuv, 0o644)
	exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
		"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", "wmv2", "-qscale:v", fmt.Sprint(q),
		"-frames:v", "1", "-g", "1", avi).Run()
	if _, err := os.Stat(avi); err != nil {
		t.Skip("ffmpeg cannot encode wmv2")
	}
	data, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-").Output()
	out, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
	return data, out
}

func TestWMV2IntraFrame(t *testing.T) {
	w, h := 96, 64
	for _, q := range []int{2, 4, 8, 12, 16, 24, 31} {
		yuv := make([]byte, w*h*3/2)
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				yuv[y*w+x] = byte(20 + (x*7+y*5)%210)
			}
		}
		for i := w * h; i < len(yuv); i++ {
			yuv[i] = byte(100 + (i*3)%56)
		}
		pkt, ref := encodeWMV2(t, w, h, q, yuv)
		if len(pkt) == 0 || len(ref) < w*h {
			t.Skip("no packet")
		}
		img, err := DecodeIntraFrameWMV2(pkt, w, h, nil)
		if err != nil {
			t.Errorf("q%d: %v", q, err)
			continue
		}
		var gotY, gotC []byte
		cw, chh := (w+1)/2, (h+1)/2
		for y := 0; y < h; y++ {
			gotY = append(gotY, img.Y[y*img.YStride:y*img.YStride+w]...)
		}
		for y := 0; y < chh; y++ {
			gotC = append(gotC, img.Cb[y*img.CStride:y*img.CStride+cw]...)
		}
		pl := psnr(gotY, ref[:w*h])
		pc := psnr(gotC, ref[w*h:w*h+cw*chh])
		t.Logf("WMV2 I q=%2d: luma=%.1f chroma=%.1f dB", q, pl, pc)
		if pl < 40 || pc < 40 {
			t.Errorf("q%d WMV2 PSNR too low: luma=%.1f chroma=%.1f", q, pl, pc)
		}
	}
}

var _ = image.Black

func TestWMV2LoopFilter(t *testing.T) {
	w, h := 96, 64
	// extradata with loop_filter=1, j_type_bit=1, per_mb_rl_bit=1 (as ffmpeg's encoder writes).
	ex := []byte{0x00, 0x00, 0xF4, 0x80}
	for _, q := range []int{4, 8, 16} {
		yuv := make([]byte, w*h*3/2)
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				yuv[y*w+x] = byte(20 + (x*7+y*5)%210)
			}
		}
		for i := w * h; i < len(yuv); i++ {
			yuv[i] = byte(100 + (i*3)%56)
		}
		dir := t.TempDir()
		in, avi := dir+"/in.yuv", dir+"/c.avi"
		os.WriteFile(in, yuv, 0o644)
		exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
			"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", "wmv2", "-qscale:v", fmt.Sprint(q),
			"-frames:v", "1", "-g", "1", "-flags", "+loop", avi).Run()
		if _, err := os.Stat(avi); err != nil {
			t.Skip("ffmpeg cannot encode wmv2")
		}
		data, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-").Output()
		ref, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
		if len(data) == 0 || len(ref) < w*h {
			t.Skip("no packet")
		}
		// without the deblocking filter the PSNR is much lower; with it, it matches ffmpeg.
		off, _ := DecodeIntraFrameWMV2(data, w, h, nil)
		on, err := DecodeIntraFrameWMV2(data, w, h, ex)
		if err != nil || off == nil || on == nil {
			t.Fatalf("q%d decode: %v", q, err)
		}
		grab := func(img *image.YCbCr) []byte {
			var g []byte
			for y := 0; y < h; y++ {
				g = append(g, img.Y[y*img.YStride:y*img.YStride+w]...)
			}
			return g
		}
		pOff := psnr(grab(off), ref[:w*h])
		pOn := psnr(grab(on), ref[:w*h])
		t.Logf("WMV2 loop-filter q=%2d: no-LF=%.1f dB  LF=%.1f dB", q, pOff, pOn)
		if pOn < 60 || pOn < pOff {
			t.Errorf("q%d loop filter did not improve PSNR (no-LF=%.1f LF=%.1f)", q, pOff, pOn)
		}
	}
}
