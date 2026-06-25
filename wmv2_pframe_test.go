package msmpeg4

import (
	"fmt"
	"image"
	"math/rand"
	"os"
	"os/exec"
	"testing"
)

// ffmpegYCbCr wraps a planar yuv420p buffer as an *image.YCbCr (used as a shared reference so the
// WMV2 P-frame decode is compared against ffmpeg from the SAME I-frame).
func ffmpegYCbCr(buf []byte, w, h int) *image.YCbCr {
	img := image.NewYCbCr(image.Rect(0, 0, w, h), image.YCbCrSubsampleRatio420)
	cw, chh := (w+1)/2, (h+1)/2
	for y := 0; y < h; y++ {
		copy(img.Y[y*img.YStride:y*img.YStride+w], buf[y*w:y*w+w])
	}
	o := w * h
	for y := 0; y < chh; y++ {
		copy(img.Cb[y*img.CStride:y*img.CStride+cw], buf[o+y*cw:o+y*cw+cw])
	}
	o += cw * chh
	for y := 0; y < chh; y++ {
		copy(img.Cr[y*img.CStride:y*img.CStride+cw], buf[o+y*cw:o+y*cw+cw])
	}
	return img
}

// TestWMV2PFrame decodes a WMV2 P-frame and compares it to ffmpeg starting from ffmpeg's own
// decoded I-frame (so the comparison isolates the P-frame decode). Integer motion -> even MVs ->
// no mspel/ABT, exercising the core path (mb_non_intra tables, MV prediction, MC, blocks, IDCT).
func TestWMV2PFrame(t *testing.T) {
	w, h := 160, 128
	rng := rand.New(rand.NewSource(5))
	for _, q := range []int{2, 3, 4, 6, 8, 12, 16} {
		f0 := make([]byte, w*h*3/2)
		f1 := make([]byte, w*h*3/2)
		for i := range f0 {
			f0[i] = byte(rng.Intn(200) + 28)
		}
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				sx, sy := x-2, y-2
				if sx < 0 {
					sx = 0
				}
				if sy < 0 {
					sy = 0
				}
				f1[y*w+x] = f0[sy*w+sx] // integer 2px shift -> even MV
			}
		}
		for i := w * h; i < len(f0); i++ {
			f1[i] = f0[i]
		}
		dir := t.TempDir()
		in, avi := dir+"/in.yuv", dir+"/c.avi"
		os.WriteFile(in, append(append([]byte{}, f0...), f1...), 0o644)
		exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
			"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", "wmv2", "-qscale:v", fmt.Sprint(q),
			"-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000", avi).Run()
		if _, err := os.Stat(avi); err != nil {
			t.Skip("ffmpeg cannot encode wmv2")
		}
		szOut, _ := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0",
			"-show_entries", "packet=size", "-of", "csv=p=0", avi).Output()
		var s0, s1 int
		fmt.Sscanf(string(szOut), "%d\n%d", &s0, &s1)
		data, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-").Output()
		out, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
		fsz := w * h * 3 / 2
		if s0+s1 > len(data) || len(out) < 2*fsz {
			t.Skip("packet/ref parse failed")
		}
		ref0 := ffmpegYCbCr(out[:fsz], w, h)
		pImg, err := DecodePFrameWMV2(data[s0:s0+s1], ref0, w, h, nil, false)
		if err != nil {
			t.Errorf("q%d P: %v", q, err)
			continue
		}
		cw, chh := (w+1)/2, (h+1)/2
		var gotY, gotC []byte
		for y := 0; y < h; y++ {
			gotY = append(gotY, pImg.Y[y*pImg.YStride:y*pImg.YStride+w]...)
		}
		for y := 0; y < chh; y++ {
			gotC = append(gotC, pImg.Cb[y*pImg.CStride:y*pImg.CStride+cw]...)
		}
		pl := psnr(gotY, out[fsz:fsz+w*h])
		pc := psnr(gotC, out[fsz+w*h:fsz+w*h+cw*chh])
		t.Logf("WMV2 P q=%2d: luma=%.1f chroma=%.1f dB (vs ffmpeg, same I-ref)", q, pl, pc)
		if pl < 55 || pc < 55 {
			t.Errorf("q%d WMV2 P PSNR too low: luma=%.1f chroma=%.1f", q, pl, pc)
		}
	}
}
