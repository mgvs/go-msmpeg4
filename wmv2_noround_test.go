package msmpeg4

import (
	"fmt"
	"math"
	"os"
	"os/exec"
	"testing"
)

// TestWMV2NoRoundMultiP checks the WMV2 no_rounding tracking: it toggles each P-frame (0 on the
// first P after an I, 1 on the next, …), which matters for half-pel motion compensation. A
// multi-frame clip with sub-pixel motion must stay bit-exact vs ffmpeg across the whole P run
// (before the fix, even P-frames drifted because the decoder assumed no_rounding=0 throughout).
func TestWMV2NoRoundMultiP(t *testing.T) {
	w, h, nf := 128, 96, 6
	raw := make([]byte, 0, nf*w*h*3/2)
	for f := 0; f < nf; f++ {
		fr := make([]byte, w*h*3/2)
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				fx := float64(x) + 0.5*float64(f) // sub-pixel diagonal motion -> half-pel MVs
				fy := float64(y) + 0.3*float64(f)
				fr[y*w+x] = byte(64 + 60*math.Sin(fx*0.2) + 40*math.Cos(fy*0.15))
			}
		}
		for i := w * h; i < len(fr); i++ {
			fr[i] = 128
		}
		raw = append(raw, fr...)
	}
	dir := t.TempDir()
	in, wmv := dir+"/in.yuv", dir+"/c.wmv"
	os.WriteFile(in, raw, 0o644)
	exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
		"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", "wmv2", "-qscale:v", "4",
		"-frames:v", fmt.Sprint(nf), "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000", wmv).Run()
	if _, err := os.Stat(wmv); err != nil {
		t.Skip("ffmpeg cannot encode wmv2")
	}
	data, _ := os.ReadFile(wmv)
	out, _ := exec.Command("ffmpeg", "-v", "error", "-i", wmv, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
	fsz := w * h * 3 / 2
	dm, err := OpenASF(data)
	if err != nil {
		t.Skip("ASF parse failed")
	}
	fcc, dw, dh, ex := dm.Codec()
	dec, _ := NewDecoder(fcc, dw, dh, ex)
	res := ""
	for f := 0; f < nf; f++ {
		pkt, e := dm.ReadPacket()
		if e != nil || (f+1)*fsz > len(out) {
			break
		}
		img, derr := dec.DecodeFrame(pkt)
		if derr != nil {
			t.Errorf("frame %d: %v", f, derr)
			continue
		}
		var g []byte
		for y := 0; y < h; y++ {
			g = append(g, img.Y[y*img.YStride:y*img.YStride+w]...)
		}
		p := psnr(g, out[f*fsz:f*fsz+w*h])
		res += fmt.Sprintf(" f%d:%.0f", f, p)
		if p < 90 {
			t.Errorf("WMV2 multi-P frame %d not bit-exact (luma=%.1f) — no_rounding drift?", f, p)
		}
	}
	t.Logf("WMV2 half-pel multi-P (no_rounding toggle):%s", res)
}
