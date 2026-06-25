package msmpeg4

import (
	"fmt"
	"os"
	"os/exec"
	"testing"
)

// encodeV2Clip encodes a 2-frame MS-MPEG4 v2 clip (I + P) and returns the two packets plus
// ffmpeg's decoded P-frame YUV (reference).
func encodeV2Clip(t *testing.T, w, h, q int, f0, f1 []byte) (iPkt, pPkt, ref1 []byte) {
	t.Helper()
	dir := t.TempDir()
	in, avi := dir+"/in.yuv", dir+"/c.avi"
	os.WriteFile(in, append(append([]byte{}, f0...), f1...), 0o644)
	exec.Command("ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
		"-s", fmt.Sprintf("%dx%d", w, h), "-i", in, "-c:v", "msmpeg4v2", "-qscale:v", fmt.Sprint(q),
		"-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000", "-vtag", "MP42", avi).Run()
	if _, err := os.Stat(avi); err != nil {
		t.Skip("ffmpeg cannot encode msmpeg4v2")
	}
	szOut, _ := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0",
		"-show_entries", "packet=size", "-of", "csv=p=0", avi).Output()
	var s0, s1 int
	fmt.Sscanf(string(szOut), "%d\n%d", &s0, &s1)
	data, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-").Output()
	out, _ := exec.Command("ffmpeg", "-v", "error", "-i", avi, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
	if s0+s1 > len(data) || len(out) < w*h*3 {
		t.Skip("packet/ref parse failed")
	}
	return data[:s0], data[s0 : s0+s1], out[w*h*3/2 : w*h*3]
}

func TestV2PFrame(t *testing.T) {
	w, h := 112, 80
	for _, q := range []int{1, 3, 6, 8, 12, 16} {
		f0 := make([]byte, w*h*3/2)
		f1 := make([]byte, w*h*3/2)
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				f0[y*w+x] = byte(30 + (x*5+y*3)%200)
				sx := x - 3
				if sx < 0 {
					sx = 0
				}
				f1[y*w+x] = byte(30 + (sx*5+y*3)%200) // horizontal motion
			}
		}
		for i := w * h; i < len(f0); i++ {
			f0[i] = byte(100 + (i*3)%50)
			f1[i] = f0[i]
		}
		iPkt, pPkt, ref1 := encodeV2Clip(t, w, h, q, f0, f1)
		if len(pPkt) == 0 {
			t.Skip("no P packet")
		}
		iImg, err := DecodeIntraFrameV2(iPkt, w, h)
		if err != nil {
			t.Fatalf("q%d I: %v", q, err)
		}
		pImg, err := DecodePFrameV2(pPkt, iImg, w, h)
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
		pl := psnr(gotY, ref1[:w*h])
		pc := psnr(gotC, ref1[w*h:w*h+cw*chh])
		t.Logf("v2 P-frame q=%2d: luma=%.1f chroma=%.1f dB", q, pl, pc)
		if pl < 45 || pc < 45 {
			t.Errorf("q%d v2 P PSNR too low: luma=%.1f chroma=%.1f", q, pl, pc)
		}
	}
}
