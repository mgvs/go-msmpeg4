package msmpeg4

import (
	"encoding/binary"
	"io"
	"os"
	"testing"
)

// TestRealFiles decodes the first intra frame from real DIV3 AVI files.
func TestRealFiles(t *testing.T) {
	cases := []struct {
		path string
		w, h int
	}{
		{"testdata/movie1.avi", 512, 288},
		{"testdata/movie2.avi", 576, 240},
		{"testdata/movie3.avi", 512, 354},
		{"testdata/movie4.avi", 576, 256},
		{"testdata/movie5.avi", 512, 384},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.path[len("testdata/"):], func(t *testing.T) {
			frame := firstVideoFrame(t, tc.path)
			if frame == nil {
				t.Skip("no frame found")
			}
			_, err := DecodeIntraFrame(frame, tc.w, tc.h)
			if err != nil {
				t.Errorf("decode: %v", err)
			}
		})
	}
}

// firstVideoFrame extracts the first non-empty 00dc/00db chunk from an AVI.
func firstVideoFrame(t *testing.T, path string) []byte {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Skip("file not found:", path)
		return nil
	}
	defer f.Close()

	data, err := io.ReadAll(io.LimitReader(f, 500*1024))
	if err != nil {
		t.Fatal(err)
	}

	moviIdx := indexOf(data, []byte("movi"))
	if moviIdx < 4 {
		t.Skip("no movi chunk")
		return nil
	}
	moviSize := int(binary.LittleEndian.Uint32(data[moviIdx-4:]))
	end := moviIdx + moviSize - 4
	if end > len(data) {
		end = len(data)
	}

	p := moviIdx + 4
	for p+8 <= end {
		fourcc := data[p : p+4]
		sz := int(binary.LittleEndian.Uint32(data[p+4:]))
		if isVideoChunk(fourcc) && sz > 0 && p+8+sz <= len(data) {
			return data[p+8 : p+8+sz]
		}
		p += 8 + sz + (sz & 1)
	}
	return nil
}

// videoFrames returns up to n video frame payloads from an AVI file.
func videoFrames(t *testing.T, path string, n int) [][]byte {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Skip("file not found:", path)
		return nil
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 20*1024*1024))
	if err != nil {
		t.Fatal(err)
	}
	moviIdx := indexOf(data, []byte("movi"))
	if moviIdx < 4 {
		t.Skip("no movi chunk")
		return nil
	}
	moviSize := int(binary.LittleEndian.Uint32(data[moviIdx-4:]))
	end := moviIdx + moviSize - 4
	if end > len(data) {
		end = len(data)
	}
	var out [][]byte
	p := moviIdx + 4
	for p+8 <= end && len(out) < n {
		fourcc := data[p : p+4]
		sz := int(binary.LittleEndian.Uint32(data[p+4:]))
		if isVideoChunk(fourcc) && sz > 0 && p+8+sz <= len(data) {
			out = append(out, data[p+8:p+8+sz])
		}
		p += 8 + sz + (sz & 1)
	}
	return out
}

// TestRealPFrames tries to decode the first I+P frame pair from each real AVI.
func TestRealPFrames(t *testing.T) {
	cases := []struct {
		name string
		path string
		w, h int
	}{
		{"movie1", "testdata/movie1.avi", 512, 288},
		{"movie2", "testdata/movie2.avi", 576, 240},
		{"movie3", "testdata/movie3.avi", 512, 354},
		{"movie4", "testdata/movie4.avi", 576, 256},
		{"movie5", "testdata/movie5.avi", 512, 384},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			frames := videoFrames(t, tc.path, 5)
			if len(frames) < 2 {
				t.Skip("fewer than 2 frames found")
			}
			// Find first I-frame and the P-frame that follows it.
			var iFrame []byte
			for _, fr := range frames {
				if FrameType(fr) == picIntra {
					iFrame = fr
					break
				}
			}
			if iFrame == nil {
				t.Skip("no I-frame in first 5 frames")
			}
			refImg, err := DecodeIntraFrame(iFrame, tc.w, tc.h)
			if err != nil {
				t.Fatalf("I-frame decode: %v", err)
			}
			var decoded int
			for _, fr := range frames {
				if FrameType(fr) != picInter {
					continue
				}
				_, err := DecodePFrame(fr, refImg, tc.w, tc.h)
				if err != nil {
					t.Logf("P-frame decode error: %v", err)
				} else {
					decoded++
					t.Logf("P-frame decoded OK")
				}
				break
			}
			if decoded == 0 {
				t.Errorf("no P-frame decoded successfully")
			}
		})
	}
}

func isVideoChunk(fourcc []byte) bool {
	if len(fourcc) < 4 {
		return false
	}
	if fourcc[0] != '0' || fourcc[1] != '0' {
		return false
	}
	sfx := string(fourcc[2:])
	return sfx == "dc" || sfx == "db"
}

func indexOf(data, needle []byte) int {
	for i := 0; i+len(needle) <= len(data); i++ {
		ok := true
		for j, b := range needle {
			if data[i+j] != b {
				ok = false
				break
			}
		}
		if ok {
			return i
		}
	}
	return -1
}
