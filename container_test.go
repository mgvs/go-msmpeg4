package msmpeg4

import (
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"testing"
)

// TestContainerDemux encodes a multi-frame clip into AVI (MS-MPEG4 v2/v3) and ASF/.wmv
// (WMV1/WMV2) containers, then decodes the whole stream with Open + DecodeAll and checks every
// frame is bit-exact vs ffmpeg. It covers the demuxers end to end with the stateful decoder.
func TestContainerDemux(t *testing.T) {
	w, h := 128, 96
	const nf = 5
	cases := []struct{ codec, ext string }{
		{"msmpeg4", "avi"},
		{"msmpeg4v2", "avi"},
		{"wmv2", "wmv"},
		{"wmv1", "wmv"},
	}
	for _, cs := range cases {
		rng := rand.New(rand.NewSource(3))
		base := make([]byte, w*h*3/2)
		for i := range base {
			base[i] = byte(rng.Intn(180) + 38)
		}
		raw := make([]byte, 0, nf*len(base))
		for f := 0; f < nf; f++ {
			fr := make([]byte, w*h*3/2)
			for y := 0; y < h; y++ {
				for x := 0; x < w; x++ {
					sx := x - f
					if sx < 0 {
						sx = 0
					}
					fr[y*w+x] = base[y*w+sx]
				}
			}
			for i := w * h; i < len(fr); i++ {
				fr[i] = base[i]
			}
			raw = append(raw, fr...)
		}
		dir := t.TempDir()
		in, cont := dir+"/in.yuv", dir+"/c."+cs.ext
		os.WriteFile(in, raw, 0o644)
		exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
			"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", cs.codec, "-qscale:v", "4",
			"-frames:v", fmt.Sprint(nf), "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000", cont).Run()
		if _, err := os.Stat(cont); err != nil {
			t.Skipf("ffmpeg cannot produce %s/%s", cs.codec, cs.ext)
		}
		data, _ := os.ReadFile(cont)
		out, _ := exec.Command("ffmpeg", "-v", "error", "-i", cont, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
		fsz := w * h * 3 / 2

		dm, err := Open(data)
		if err != nil {
			t.Errorf("%s Open: %v", cs.codec, err)
			continue
		}
		fcc, dw, dh, _ := dm.Codec()
		if dw != w || dh != h {
			t.Errorf("%s demux dims %dx%d != %dx%d", cs.codec, dw, dh, w, h)
		}
		imgs, err := DecodeAll(data)
		if err != nil {
			t.Errorf("%s DecodeAll: %v", cs.codec, err)
		}
		if len(imgs) < nf {
			t.Errorf("%s got %d frames, want %d", cs.codec, len(imgs), nf)
		}
		res := ""
		for f := 0; f < len(imgs) && f < nf; f++ {
			var g []byte
			for y := 0; y < h; y++ {
				g = append(g, imgs[f].Y[y*imgs[f].YStride:y*imgs[f].YStride+w]...)
			}
			p := psnr(g, out[f*fsz:f*fsz+w*h])
			res += fmt.Sprintf(" f%d:%.0f", f, p)
			if p < 90 {
				t.Errorf("%s [%s] frame %d luma PSNR=%.1f (not bit-exact)", cs.codec, fcc, f, p)
			}
		}
		t.Logf("%s/%s [%s %dx%d] %d frames:%s", cs.codec, cs.ext, fcc, dw, dh, len(imgs), res)
	}
}
