package msmpeg4

import (
	"bytes"
	"fmt"
	"image"
	"math"
	"os"
	"os/exec"
	"testing"
)

// encodeWMV1 writes a YUV420p frame, encodes it as WMV1, and returns the video packet bytes
// plus ffmpeg's own decoded YUV (the reference).
func encodeWMV1(t *testing.T, w, h, q int, yuv []byte) (packet, refYUV []byte) {
	t.Helper()
	dir := t.TempDir()
	in := dir + "/in.yuv"
	avi := dir + "/c.avi"
	if err := os.WriteFile(in, yuv, 0o644); err != nil {
		t.Fatal(err)
	}
	run := func(args ...string) []byte {
		var out bytes.Buffer
		c := exec.Command("ffmpeg", args...)
		c.Stdout = &out
		if err := c.Run(); err != nil {
			t.Skipf("ffmpeg unavailable or failed: %v", err)
		}
		return out.Bytes()
	}
	exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
		"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", "wmv1", "-qscale:v", fmt.Sprint(q),
		"-frames:v", "1", avi).Run()
	if _, err := os.Stat(avi); err != nil {
		t.Skip("ffmpeg could not encode wmv1")
	}
	packet = run("-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-")
	refYUV = run("-v", "error", "-i", avi, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-")
	return packet, refYUV
}

func psnr(a, b []byte) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0
	}
	var se float64
	for i := range a {
		d := float64(a[i]) - float64(b[i])
		se += d * d
	}
	mse := se / float64(len(a))
	if mse == 0 {
		return math.Inf(1)
	}
	return 10 * math.Log10(255*255/mse)
}

func TestWMV1IntraFrame(t *testing.T) {
	w, h := 64, 48
	q := 4
	// content: smooth gradient + a textured patch so AC coefficients are exercised
	yuv := make([]byte, w*h*3/2)
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			v := 40 + x*2 + y
			if (x/4+y/4)%2 == 0 {
				v += 30
			}
			if v > 255 {
				v = 255
			}
			yuv[y*w+x] = byte(v)
		}
	}
	for i := w * h; i < len(yuv); i++ {
		yuv[i] = 128
	}
	packet, ref := encodeWMV1(t, w, h, q, yuv)
	if len(packet) == 0 || len(ref) < w*h*3/2 {
		t.Skip("no packet/ref from ffmpeg")
	}
	img, err := DecodeIntraFrameWMV1(packet, w, h)
	if err != nil {
		t.Fatalf("DecodeIntraFrameWMV1: %v", err)
	}
	// assemble our YUV (planar 4:2:0) from the image
	cw, chh := (w+1)/2, (h+1)/2
	got := make([]byte, 0, len(ref))
	for y := 0; y < h; y++ {
		got = append(got, img.Y[y*img.YStride:y*img.YStride+w]...)
	}
	for y := 0; y < chh; y++ {
		got = append(got, img.Cb[y*img.CStride:y*img.CStride+cw]...)
	}
	for y := 0; y < chh; y++ {
		got = append(got, img.Cr[y*img.CStride:y*img.CStride+cw]...)
	}
	p := psnr(got[:w*h], ref[:w*h]) // luma PSNR
	t.Logf("WMV1 luma PSNR vs ffmpeg = %.2f dB (frame %dx%d q%d)", p, w, h, q)
	if p < 30 {
		// dump first mismatching pixels for debugging
		for i := 0; i < w*h && i < 64; i++ {
			if got[i] != ref[i] {
				t.Logf("  diff at %d: got %d ref %d", i, got[i], ref[i])
			}
		}
		t.Fatalf("WMV1 luma PSNR too low: %.2f dB", p)
	}
}

func TestWMV1Sweep(t *testing.T) {
	w, h := 80, 64
	for _, q := range []int{1, 2, 4, 8, 12, 16, 24, 31} {
		yuv := make([]byte, w*h*3/2)
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				v := 20 + ((x*x + y*y*3) % 200) + (x^y)%40 // busy content -> lots of AC / escapes
				if v > 255 {
					v %= 256
				}
				yuv[y*w+x] = byte(v)
			}
		}
		for i := w * h; i < len(yuv); i++ {
			yuv[i] = byte(96 + (i*7)%64) // non-flat chroma
		}
		packet, ref := encodeWMV1(t, w, h, q, yuv)
		if len(packet) == 0 || len(ref) < w*h*3/2 {
			t.Skip("no packet/ref")
		}
		img, err := DecodeIntraFrameWMV1(packet, w, h)
		if err != nil {
			t.Errorf("q=%d: decode error: %v", q, err)
			continue
		}
		cw, chh := (w+1)/2, (h+1)/2
		var got []byte
		for y := 0; y < h; y++ {
			got = append(got, img.Y[y*img.YStride:y*img.YStride+w]...)
		}
		cb := []byte{}
		for y := 0; y < chh; y++ {
			cb = append(cb, img.Cb[y*img.CStride:y*img.CStride+cw]...)
		}
		pl := psnr(got, ref[:w*h])
		pc := psnr(cb, ref[w*h:w*h+cw*chh])
		t.Logf("q=%2d: luma PSNR=%.1f dB  chroma Cb PSNR=%.1f dB", q, pl, pc)
		if pl < 35 || pc < 35 {
			t.Errorf("q=%d: PSNR too low (luma %.1f, chroma %.1f)", q, pl, pc)
		}
	}
}

// encodeWMV1Clip encodes a 2-frame WMV1 clip and returns the I & P packets + ffmpeg's decoded
// YUV for both frames (reference).
func encodeWMV1Clip(t *testing.T, w, h, q int, f0, f1 []byte) (iPkt, pPkt, ref0, ref1 []byte) {
	t.Helper()
	dir := t.TempDir()
	in := dir + "/in.yuv"
	avi := dir + "/c.avi"
	os.WriteFile(in, append(append([]byte{}, f0...), f1...), 0o644)
	exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
		"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", "wmv1", "-qscale:v", fmt.Sprint(q),
		"-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000", avi).Run()
	if _, err := os.Stat(avi); err != nil {
		t.Skip("ffmpeg could not encode wmv1 clip")
	}
	szOut, _ := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0",
		"-show_entries", "packet=size", "-of", "csv=p=0", avi).Output()
	var s0, s1 int
	fmt.Sscanf(string(szOut), "%d\n%d", &s0, &s1)
	data, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-").Output()
	if s0+s1 > len(data) {
		t.Skip("packet parse failed")
	}
	iPkt, pPkt = data[:s0], data[s0:s0+s1]
	out, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
	fsz := w * h * 3 / 2
	ref0, ref1 = out[:fsz], out[fsz:2*fsz]
	return
}

func wmv1BitRate(pkt []byte) int { // parse the I-frame ext-header bit_rate
	r := newBitReader(pkt)
	r.u(2)
	r.u(5)
	r.u(5)
	r.u(5)
	return r.u(11) * 1024
}

func planarYUV(img *image.YCbCr, w, h int) []byte {
	cw, chh := (w+1)/2, (h+1)/2
	out := make([]byte, 0, w*h+2*cw*chh)
	for y := 0; y < h; y++ {
		out = append(out, img.Y[y*img.YStride:y*img.YStride+w]...)
	}
	for y := 0; y < chh; y++ {
		out = append(out, img.Cb[y*img.CStride:y*img.CStride+cw]...)
	}
	for y := 0; y < chh; y++ {
		out = append(out, img.Cr[y*img.CStride:y*img.CStride+cw]...)
	}
	return out
}

func TestWMV1PFrame(t *testing.T) {
	w, h := 96, 64
	for _, q := range []int{3, 8, 16} {
		f0 := make([]byte, w*h*3/2)
		f1 := make([]byte, w*h*3/2)
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				f0[y*w+x] = byte(40 + (x*3+y*2)%180)
				f1[y*w+x] = byte(40 + ((x+2)*3+(y+1)*2)%180) // shifted -> motion
			}
		}
		for i := w * h; i < len(f0); i++ {
			f0[i], f1[i] = 128, 128
		}
		iPkt, pPkt, _, ref1 := encodeWMV1Clip(t, w, h, q, f0, f1)
		if len(iPkt) == 0 || len(pPkt) == 0 {
			t.Skip("no packets")
		}
		iImg, err := DecodeIntraFrameWMV1(iPkt, w, h)
		if err != nil {
			t.Fatalf("q%d I: %v", q, err)
		}
		pImg, err := DecodePFrameWMV1(pPkt, iImg, w, h, wmv1BitRate(iPkt))
		if err != nil {
			t.Errorf("q%d P: %v", q, err)
			continue
		}
		got := planarYUV(pImg, w, h)
		pl := psnr(got[:w*h], ref1[:w*h])
		t.Logf("WMV1 P-frame q=%2d: luma PSNR=%.1f dB", q, pl)
		if pl < 30 {
			t.Errorf("q%d P luma PSNR too low: %.1f", q, pl)
		}
	}
}
