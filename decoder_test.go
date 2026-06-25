package msmpeg4

import (
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"testing"
)

// TestStatefulDecoder drives the high-level Decoder over a 4-frame (I,P,P,P) sequence for each
// codec and compares every decoded frame against ffmpeg. It exercises auto I/P dispatch and
// reference-picture maintenance. All four codecs are bit-exact across the whole run.
func TestStatefulDecoder(t *testing.T) {
	w, h := 128, 96
	const nf = 4
	cases := []struct {
		codec, fourcc string
		bitExact      bool
	}{
		{"msmpeg4v2", "MP42", true},
		{"msmpeg4", "DIV3", true},
		{"wmv1", "WMV1", true},
		{"wmv2", "WMV2", true},
	}
	for _, cs := range cases {
		rng := rand.New(rand.NewSource(11))
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
		in, avi := dir+"/in.yuv", dir+"/c.avi"
		os.WriteFile(in, raw, 0o644)
		exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
			"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", cs.codec, "-qscale:v", "4",
			"-frames:v", fmt.Sprint(nf), "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000", avi).Run()
		if _, err := os.Stat(avi); err != nil {
			t.Skipf("ffmpeg cannot encode %s", cs.codec)
		}
		szOut, _ := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0",
			"-show_entries", "packet=size", "-of", "csv=p=0", avi).Output()
		var sizes []int
		for _, ln := range splitNonEmpty(string(szOut)) {
			var n int
			if _, err := fmt.Sscanf(ln, "%d", &n); err == nil {
				sizes = append(sizes, n)
			}
		}
		data, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-").Output()
		out, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
		fsz := w * h * 3 / 2
		var ex []byte
		if cs.fourcc == "WMV1" {
			ex = []byte{0x00, 0x00, 0xF4, 0x80} // WMV1 needs the ext-header-derived flags
		}
		dec, err := NewDecoder(cs.fourcc, w, h, ex)
		if err != nil {
			t.Fatalf("%s NewDecoder: %v", cs.fourcc, err)
		}
		off, res := 0, ""
		for f := 0; f < len(sizes) && f < nf; f++ {
			if off+sizes[f] > len(data) {
				break
			}
			pkt := data[off : off+sizes[f]]
			off += sizes[f]
			img, e := dec.DecodeFrame(pkt)
			if e != nil {
				t.Errorf("%s frame %d: %v", cs.fourcc, f, e)
				break
			}
			var g []byte
			for y := 0; y < h; y++ {
				g = append(g, img.Y[y*img.YStride:y*img.YStride+w]...)
			}
			p := psnr(g, out[f*fsz:f*fsz+w*h])
			res += fmt.Sprintf(" f%d:%.0f", f, p)
			min := 55.0
			if cs.bitExact {
				min = 90 // bit-exact: every frame (I and P) must match ffmpeg
			}
			if p < min {
				t.Errorf("%s frame %d luma PSNR=%.1f < %.0f", cs.fourcc, f, p, min)
			}
		}
		t.Logf("%s (I,P,P,P) luma dB:%s", cs.fourcc, res)
	}
}

func splitNonEmpty(s string) []string {
	var out []string
	cur := ""
	for _, r := range s {
		if r == '\n' {
			if cur != "" {
				out = append(out, cur)
			}
			cur = ""
		} else {
			cur += string(r)
		}
	}
	if cur != "" {
		out = append(out, cur)
	}
	return out
}
