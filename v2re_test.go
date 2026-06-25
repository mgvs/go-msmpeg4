package msmpeg4

import (
	"encoding/binary"
	"fmt"
	"os"
	"testing"
)

func v2FirstFrame(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil { return nil, err }
	defer f.Close()
	buf := make([]byte, 512*1024)
	n, _ := f.Read(buf)
	data := buf[:n]
	start, end := aviMoviRange(data)
	if start < 0 { return nil, fmt.Errorf("no movi") }
	p := start
	for p+8 <= end {
		sz := int(binary.LittleEndian.Uint32(data[p+4:p+8]))
		if sz > 0 && p+8+sz <= len(data) && aviIsVideoCk(data[p:p+4]) {
			frame := data[p+8:p+8+sz]
			if FrameType(frame) == picIntra { return frame, nil }
		}
		if sz < 0 { break }
		p += 8 + sz + (sz & 1)
	}
	return nil, fmt.Errorf("no I-frame")
}

// skipIfNoFixture skips a scratch RE test when its /tmp fixture is absent (these tests depend
// on externally-generated AVI fixtures that may not exist).
func skipIfNoFixture(t *testing.T, err error) {
	if err != nil && os.IsNotExist(err) {
		t.Skip("scratch fixture missing")
	}
}

func TestV2REDump(t *testing.T) {
	for _, tc := range []struct{ name, path, desc string }{
		{"red",   "/tmp/v2_red.avi",   "Y≈81 Cb≈90 Cr≈240"},
		{"blue",  "/tmp/v2_blue.avi",  "Y≈29 Cb≈255 Cr≈107"},
		{"white", "/tmp/v2_white.avi", "Y≈235 Cb≈Cr≈128"},
	} {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("%s: %v", tc.name, err) }
		r := newBitReader(frame)
		pictype := r.u(2); q := r.u(5); sliceCode := r.u(5)
		t.Logf("=== %s (%s) len=%d ===", tc.name, tc.desc, len(frame))
		t.Logf("  header: pictype=%d quant=%d slice_code=%d MB@bit%d", pictype, q, sliceCode, r.pos)
		mb := ""
		pos0 := r.pos
		for i := 0; i < 80; i++ {
			if i > 0 && i%8 == 0 { mb += " " }
			mb += fmt.Sprintf("%d", r.bit())
		}
		t.Logf("  MB bits [%d..+80]: %s", pos0, mb)
	}
}

// TestV2ParseMB tries to decode the v2 I-frame MB layer systematically.
// For a solid-colour 32×16 frame we expect:
//   White:  Y≈235, Cb≈128, Cr≈128
//   Red:    Y≈81,  Cb≈90,  Cr≈240
//   Blue:   Y≈29,  Cb≈255, Cr≈107
// With dc_scaler=8, dc_coeff=pixel_value, default pred=128.
// We know the DC VLC table (dcTables[0][0]) and DC diff for block 0 of MB0 = pixel_value-128.
func TestV2ParseMB(t *testing.T) {
	for _, tc := range []struct {
		name, path  string
		expY, expCb, expCr int
	}{
		{"white", "/tmp/v2_white.avi", 235, 128, 128},
		{"red",   "/tmp/v2_red.avi",   81, 90, 240},
		{"blue",  "/tmp/v2_blue.avi",  29, 255, 107},
	} {
		frame, err := v2FirstFrame(tc.path)
		if err != nil {
			skipIfNoFixture(t, err)
			t.Fatalf("%s: %v", tc.name, err)
		}
		r := newBitReader(frame)
		r.u(2); r.u(5); r.u(5) // skip picture header

		dc := dcTables[0][0]
		dcC := dcTables[0][1]
		defL := (2048 + 8) / (2 * 8) // round(1024/8) = 128
		defC := (2048 + 8) / (2 * 8) // same for chroma in v2

		// Expected DC diff for block 0 (luma): Y - 128
		expDiff0 := tc.expY - defL
		expDiffCb := tc.expCb - defC
		expDiffCr := tc.expCr - defC
		t.Logf("=== %s: expDiff0=%d expDiffCb=%d expDiffCr=%d ===", tc.name, expDiff0, expDiffCb, expDiffCr)
		t.Logf("  bits remaining at pos %d: ", r.pos)

		// Try to find what bit position the DC for block 0 starts at
		// by searching for the known VLC code for diff=expDiff0.
		// We'll try all bit positions from 12 to 32 as possible DC start.
		_ = dc; _ = dcC
		_ = defL; _ = defC
		_ = expDiff0; _ = expDiffCb; _ = expDiffCr

		// Brute-force: at each pos, try to read DC for 6 blocks and see if they make sense
		frameBits := make([]int, len(frame)*8)
		for i := range frameBits {
			b := frame[i/8]
			frameBits[i] = int((b >> (7 - uint(i%8))) & 1)
		}
		t.Logf("  frame bits [12..40]: %v", frameBits[12:40])

		// Show what VLC code corresponds to diff=(Y-128) from dcTables[0][0]
		mag := tc.expY - 128
		if mag < 0 { mag = -mag }
		for code, m := range dcTables[0][0].vlc {
			if m == mag {
				t.Logf("  dcTables[0][0] VLC for mag=%d: %q (%d bits)", mag, code, len(code))
			}
		}
		for code, m := range dcTables[0][1].vlc {
			if m == mag {
				t.Logf("  dcTables[0][1] VLC for mag=%d: %q (%d bits)", mag, code, len(code))
			}
		}
		for code, m := range dcTables[1][0].vlc {
			if m == mag {
				t.Logf("  dcTables[1][0] VLC for mag=%d: %q (%d bits)", mag, code, len(code))
			}
		}
	}
}

// TestV2SearchDCCode searches for the DC VLC code for the expected Y diff in the bitstream.
func TestV2SearchDCCode(t *testing.T) {
	for _, tc := range []struct {
		name, path  string
		expY        int
	}{
		{"white", "/tmp/v2_white.avi", 235},
		{"red",   "/tmp/v2_red.avi",   81},
		{"blue",  "/tmp/v2_blue.avi",  29},
	} {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("%s: %v", tc.name, err) }

		// Build bit string from frame
		bits := make([]byte, len(frame)*8)
		for i := range bits {
			bits[i] = (frame[i/8] >> (7 - uint(i%8))) & 1
		}
		bs := ""
		for _, b := range bits { bs += fmt.Sprintf("%d", b) }

		// Find VLC codes for luma diff = expY - 128
		mag := tc.expY - 128
		if mag < 0 { mag = -mag }
		t.Logf("=== %s: looking for luma diff=%d mag=%d in bitstream ===", tc.name, tc.expY-128, mag)
		for tableIdx := 0; tableIdx < 2; tableIdx++ {
			for isChroma := 0; isChroma < 2; isChroma++ {
				tbl := dcTables[tableIdx][isChroma]
				for code, m := range tbl.vlc {
					if m == mag {
						// Search in the bitstream
						pos := 0
						for {
							found := -1
							for p := pos; p <= len(bs)-len(code); p++ {
								if bs[p:p+len(code)] == code {
									found = p
									break
								}
							}
							if found < 0 { break }
							t.Logf("  table[%d][%d] code=%q found at bit %d (= frame byte %d)",
								tableIdx, isChroma, code, found, found/8)
							pos = found + 1
						}
					}
				}
			}
		}

		// Also try H.263-style 8-bit absolute DC (dc_quant = expY)
		target8 := fmt.Sprintf("%08b", tc.expY)
		for p := 12; p <= len(bs)-8; p++ {
			if bs[p:p+8] == target8 {
				t.Logf("  8-bit abs DC=%d found at bit %d", tc.expY, p)
			}
		}
	}
}

// TestV2DumpGray dumps the raw MB bits for gray frames to probe DC table.
func TestV2DumpGray(t *testing.T) {
	for _, tc := range []struct{ name, path string }{
		{"gray128", "/tmp/v2_gray128.avi"},
		{"gray129", "/tmp/v2_gray129.avi"},
		{"black",   "/tmp/v2_black.avi"},
		{"white",   "/tmp/v2_white.avi"},
	} {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("%s: %v", tc.name, err) }

		bits := make([]byte, len(frame)*8)
		for i := range bits {
			bits[i] = (frame[i/8] >> (7 - uint(i%8))) & 1
		}
		r := newBitReader(frame)
		pictype := r.u(2); q := r.u(5); slice := r.u(5)
		_ = pictype; _ = q; _ = slice

		mb := ""
		for i := 12; i < len(bits); i++ {
			if i > 12 && (i-12)%8 == 0 { mb += " " }
			mb += fmt.Sprintf("%d", bits[i])
		}
		t.Logf("=== %s: frame bytes=%d MB bits=%d ===", tc.name, len(frame), len(frame)*8-12)
		t.Logf("  %s", mb)
	}
}

// TestV2Dump1MB dumps MB bits for single 16×16 frames.
func TestV2Dump1MB(t *testing.T) {
	for _, tc := range []struct{ name, path string }{
		{"1mb_gray128", "/tmp/v2_1mb_gray128.avi"},
		{"1mb_gray129", "/tmp/v2_1mb_gray129.avi"},
		{"1mb_white",   "/tmp/v2_1mb_white.avi"},
		{"1mb_black",   "/tmp/v2_1mb_black.avi"},
	} {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("%s: %v", tc.name, err) }

		bits := make([]byte, len(frame)*8)
		for i := range bits {
			bits[i] = (frame[i/8] >> (7 - uint(i%8))) & 1
		}
		r := newBitReader(frame)
		r.u(2); r.u(5); r.u(5) // skip header

		mb := ""
		for i := 12; i < len(bits); i++ {
			if i > 12 && (i-12)%8 == 0 { mb += " " }
			mb += fmt.Sprintf("%d", bits[i])
		}
		t.Logf("=== %s: frame=%d MB_bits=%d ===", tc.name, len(frame), len(frame)*8-12)
		t.Logf("  %s", mb)
	}
}

// TestV2FindMBBoundary finds where MB0 ends in 2MB frames by comparing with 1MB.
func TestV2FindMBBoundary(t *testing.T) {
	for _, colorName := range []string{"gray128", "gray129"} {
		frame1mb, err := v2FirstFrame("/tmp/v2_1mb_" + colorName + ".avi")
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("1mb_%s: %v", colorName, err) }
		frame2mb, err := v2FirstFrame("/tmp/v2_" + colorName + ".avi")
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("2mb_%s: %v", colorName, err) }

		bits1 := frameToBitString(frame1mb)
		bits2 := frameToBitString(frame2mb)

		// Find longest common prefix (starting at MB layer = bit 12)
		lcp := 0
		for i := 12; i < len(bits1) && i < len(bits2); i++ {
			if bits1[i] != bits2[i] { break }
			lcp = i + 1 - 12
		}
		t.Logf("=== %s ===", colorName)
		t.Logf("  1MB MB bits=%d  2MB MB bits=%d", len(bits1)-12, len(bits2)-12)
		t.Logf("  Longest common prefix in MB layer: %d bits", lcp)
		t.Logf("  → MB0 ends somewhere around bit %d (first divergence)", lcp)
		t.Logf("  1MB bits [0..%d]: %s", lcp+8, bits1[12:12+lcp+8])
		t.Logf("  2MB bits [0..%d]: %s", lcp+8, bits2[12:12+lcp+8])
	}
}

func frameToBitString(data []byte) string {
	s := make([]byte, len(data)*8)
	for i := range s {
		s[i] = '0' + (data[i/8]>>(7-uint(i%8)))&1
	}
	return string(s)
}

// TestV2LocateHeaders finds all `100011` occurrences in MB layer to confirm MB boundaries.
func TestV2LocateHeaders(t *testing.T) {
	for _, tc := range []struct{ name, path string }{
		{"1mb_gray129",  "/tmp/v2_1mb_gray129.avi"},
		{"2mb_gray129",  "/tmp/v2_gray129.avi"},
		{"3mb_gray129",  "/tmp/v2_3mb_gray129.avi"},
		{"1mb_gray128",  "/tmp/v2_1mb_gray128.avi"},
		{"2mb_gray128",  "/tmp/v2_gray128.avi"},
		{"3mb_gray128",  "/tmp/v2_3mb_gray128.avi"},
		{"3mb_white",    "/tmp/v2_3mb_white.avi"},
	} {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("%s: %v", tc.name, err) }
		bs := frameToBitString(frame)
		// Search for "100011" in MB layer (starting at bit 12)
		const overhead = "100011"
		var positions []int
		for i := 12; i <= len(bs)-6; i++ {
			if bs[i:i+6] == overhead {
				positions = append(positions, i-12) // relative to MB layer
			}
		}
		t.Logf("=== %s: MB_bits=%d, '%s' at positions: %v ===", tc.name, len(bs)-12, overhead, positions)
	}
}

// TestV2FullDump3MB dumps complete bit sequences for 3MB frames.
func TestV2FullDump3MB(t *testing.T) {
	for _, tc := range []struct{ name, path string }{
		{"3mb_gray129", "/tmp/v2_3mb_gray129.avi"},
		{"3mb_gray128", "/tmp/v2_3mb_gray128.avi"},
	} {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { skipIfNoFixture(t, err); t.Fatalf("%s: %v", tc.name, err) }
		bs := frameToBitString(frame)
		mbBits := bs[12:]
		t.Logf("=== %s: frame=%d MB_bits=%d ===", tc.name, len(frame), len(mbBits))
		// Print in groups of 8 with positions
		for i := 0; i < len(mbBits); i += 8 {
			end := i + 8
			if end > len(mbBits) { end = len(mbBits) }
			t.Logf("  [%2d-%2d]: %s", i, end-1, mbBits[i:end])
		}
	}
}

// TestV2DecodeDC tries different DC decode hypotheses for MB1 (all-diff=0) of 3mb_gray129.
// MB1 starts at bit 22, overhead 6 bits, then 16 bits for 6 DC blocks.
// Expected: all 4 luma DC_quant=127, both chroma DC_quant=128 (diff=0 from predictors).
// Also tries absolute DC (no prediction).
func TestV2DecodeDC(t *testing.T) {
	frame, err := v2FirstFrame("/tmp/v2_3mb_gray129.avi")
	if err != nil { skipIfNoFixture(t, err); t.Fatalf("v2: %v", err) }

	// MB1 is known to start at bit 22 of the MB layer (= frame bit 34).
	// Overhead is 6 bits. DC blocks start at frame bit 40 (= MB layer bit 28).
	// We have 16 bits for 6 DC values: bits 28..43 of MB layer.
	bits := frameToBitString(frame)
	mb1DC := bits[12+28 : 12+44] // 16 bits starting at MB layer bit 28
	t.Logf("MB1 DC bits (16): %s", mb1DC)

	// Hypothesis A: try v3 dcTables[0][0] for all 6 blocks
	{
		r := newBitReader([]byte{})
		// Feed the 16 bits as a byte sequence
		// bit-pack the 16-bit string into bytes
		b0, b1 := byte(0), byte(0)
		for i, c := range mb1DC {
			if i < 8 && c == '1' { b0 |= 1 << (7 - uint(i)) }
			if i >= 8 && c == '1' { b1 |= 1 << (7 - uint(i-8)) }
		}
		r = newBitReader([]byte{b0, b1})
		vals := make([]int, 6)
		ok := true
		pred := 128 // default predictor for luma (round(1024/8)=128)
		for i := 0; i < 6; i++ {
			tbl := dcTables[0][0]
			if i >= 4 { tbl = dcTables[0][1] }
			v, success := tbl.decode(r)
			if !success { ok = false; break }
			pred += v
			vals[i] = pred
			if i == 3 { pred = 128 } // reset for chroma
		}
		t.Logf("Hyp A (v3 DC tables, pred=128): ok=%v vals=%v (expect [127 127 127 127 128 128])", ok, vals)
	}

	// Hypothesis B: 8-bit absolute DC for each block (H.263 style)
	{
		b0, b1 := byte(0), byte(0)
		for i, c := range mb1DC {
			if i < 8 && c == '1' { b0 |= 1 << (7 - uint(i)) }
			if i >= 8 && c == '1' { b1 |= 1 << (7 - uint(i-8)) }
		}
		r := newBitReader([]byte{b0, b1})
		// Only 2 blocks fit in 16 bits at 8 bits each
		v0, v1 := r.u(8), r.u(8)
		t.Logf("Hyp B (8-bit abs): first two blocks = %d %d (expect 127 127)", v0, v1)
	}
}

// TestV2DCSignDump encodes 16×16 frames around pred=128 to decode DC VLC sign convention.
func TestV2DCSignDump(t *testing.T) {
	cases := []struct{ name, path string }{
		{"Y126_neg2",  "/tmp/v2_1mb_gray128.avi"},  // existing
		{"Y127_neg1",  "/tmp/v2_1mb_gray129.avi"},  // existing
		{"Y128_zero",  "/tmp/v2_1mb_y128.avi"},
		{"Y129_pos1",  "/tmp/v2_1mb_y129.avi"},
		{"Y130_pos2",  "/tmp/v2_1mb_y130.avi"},
		{"Y235_pos107","/tmp/v2_1mb_white.avi"},    // existing
		{"Y16_neg112", "/tmp/v2_1mb_black.avi"},    // existing
	}
	// The RGB values that map to specific Y values in YUV limited (studio swing):
	// Y = 16 + 65.481*R + 128.553*G + 24.966*B (BT.601 studio)
	// For R=G=B=x: Y = 218.999*x/255 + 16 → x = (Y-16)*255/219
	// Y=128 → x ≈ 126.2 → 0x7e; Y=129 → x≈127.4 → 0x7f; Y=130 → x≈128.6 → 0x81
	// (ffmpeg -lavfi `color=c=GRAY128` uses BT.601 converstion)
	// Encode via pixel value: use -vf scale for exact YUV
	for _, tc := range []struct{ path, filter string }{
		{"/tmp/v2_1mb_y128.avi", "0x7e7e7e"},
		{"/tmp/v2_1mb_y129.avi", "0x7f7f7f"},
		{"/tmp/v2_1mb_y130.avi", "0x818181"},
	} {
		if _, err := os.Stat(tc.path); err == nil { continue }
		// Use lavfi color and force specific Y by using yuv444p source
		cmd := "ffmpeg -y -f lavfi -i 'color=c=" + tc.filter +
			":s=16x16:r=1' -frames:v 1 -c:v msmpeg4v2 -q:v 1 " + tc.path
		_ = cmd
	}
	for _, tc := range cases {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { t.Logf("%-12s SKIP: %v", tc.name, err); continue }
		bs := frameToBitString(frame)
		mb := bs[12:]
		dc := mb[6:]
		end := 20; if end > len(dc) { end = len(dc) }
		t.Logf("%-12s MB=%2d bits  overhead=%s  DC=%s", tc.name, len(mb), mb[:6], dc[:end])
	}
}

// TestV2DCBitsByRGB dumps MB0 DC bits for 1MB frames encoded from various grays.
// This maps which Y values produce which DC codes in the bitstream.
func TestV2DCBitsByRGB(t *testing.T) {
	cases := []struct{ name, path string }{
		{"1mb_gray128",    "/tmp/v2_1mb_gray128.avi"},  // Y≈126, diff=-2
		{"1mb_gray129",    "/tmp/v2_1mb_gray129.avi"},  // Y≈127, diff=-1
		{"hex808080",      "/tmp/v2_hex808080.avi"},    // 50% gray
		{"hex818181",      "/tmp/v2_hex818181.avi"},
		{"hex828282",      "/tmp/v2_hex828282.avi"},
		{"hex838383",      "/tmp/v2_hex838383.avi"},
		{"hex848484",      "/tmp/v2_hex848484.avi"},
		{"hex858585",      "/tmp/v2_hex858585.avi"},
		{"1mb_white",      "/tmp/v2_1mb_white.avi"},    // Y≈235, diff=+107
		{"1mb_black",      "/tmp/v2_1mb_black.avi"},    // Y≈16, diff=-112
	}
	for _, tc := range cases {
		frame, err := v2FirstFrame(tc.path)
		if err != nil { t.Logf("%-16s SKIP: %v", tc.name, err); continue }
		bs := frameToBitString(frame)
		mb := bs[12:]
		dc := mb[6:]
		end := 24; if end > len(dc) { end = len(dc) }
		t.Logf("%-16s MB=%2d  DC=%s", tc.name, len(mb), dc[:end])
	}
}

// TestV2LCPFull compares 1MB vs 2MB frames to get exact MB0 content size for large diffs.
func TestV2LCPFull(t *testing.T) {
	cases := []struct{ name, path1MB, path2MB string }{
		{"gray128_neg2",  "/tmp/v2_1mb_gray128.avi",  "/tmp/v2_gray128.avi"},
		{"gray129_neg1",  "/tmp/v2_1mb_gray129.avi",  "/tmp/v2_gray129.avi"},
		{"hex828282_0",   "/tmp/v2_hex808080.avi",     "/tmp/v2_gray128.avi"}, // Y=128, pred=128
		{"hex838383_p1",  "/tmp/v2_hex838383.avi",     "/tmp/v2_2mb_838383.avi"},
		{"hex858585_p2",  "/tmp/v2_hex858585.avi",     "/tmp/v2_2mb_858585.avi"},
		{"white_p107",    "/tmp/v2_1mb_white.avi",     "/tmp/v2_2mb_white.avi"},
		{"black_n112",    "/tmp/v2_1mb_black.avi",     "/tmp/v2_2mb_black.avi"},
	}
	for _, tc := range cases {
		f1, err := v2FirstFrame(tc.path1MB); if err != nil { t.Logf("%-18s SKIP1MB: %v", tc.name, err); continue }
		f2, err := v2FirstFrame(tc.path2MB); if err != nil { t.Logf("%-18s SKIP2MB: %v", tc.name, err); continue }
		bs1 := frameToBitString(f1)[12:] // MB layer
		bs2 := frameToBitString(f2)[12:]
		lcp := 0
		for lcp < len(bs1) && lcp < len(bs2) && bs1[lcp] == bs2[lcp] { lcp++ }
		// MB0 content ends at lcp (1MB has padding=0, 2MB has MB1 which starts with 100011)
		dc0 := bs1[6:] // DC bits start
		end := lcp - 6; if end > 30 { end = 30 }; if end < 0 { end = 0 }
		t.Logf("%-18s 1MB=%2d 2MB=%2d LCP=%2d MB0=%2d  Y0code=%s", tc.name,
			len(bs1), len(bs2), lcp, lcp, dc0[:end])
	}
}

// TestV2DCLenTable determines VLC_luma(len) codes by LCP analysis.
// v2 DC encoding: VLC(len) + u(len) bits. len = floor(log2(|diff|)) + 1 for diff≠0, 0 for diff=0.
// positive diff=d: u(len)=d (MSB=1 guaranteed). negative diff=-d: u(len)=d XOR ((1<<len)-1) (MSB=0).
func TestV2DCLenTable(t *testing.T) {
	cases := []struct {
		name   string
		p1MB, p2MB string
		diff int
	}{
		{"len0_diff0",   "/tmp/v2_hex828282.avi",       "/tmp/v2_2mb_diff0_828282.avi",   0},
		{"len1_neg1",    "/tmp/v2_1mb_gray129.avi",     "/tmp/v2_gray129.avi",            -1},
		{"len1_pos1",    "/tmp/v2_hex838383.avi",       "/tmp/v2_2mb_diff_p1_838383.avi", +1},
		{"len2_neg2",    "/tmp/v2_1mb_gray128.avi",     "/tmp/v2_gray128.avi",            -2},
		{"len2_pos2",    "/tmp/v2_hex858585.avi",       "/tmp/v2_2mb_diff_p2_858585.avi", +2},
		{"len3_pos4",    "/tmp/v2_1mb_diff_p4_len3.avi","/tmp/v2_2mb_diff_p4_len3.avi", +4},
		{"len4_neg8",    "/tmp/v2_1mb_diff_n8_len4.avi","/tmp/v2_2mb_diff_n8_len4.avi", -8},
		{"len5_pos16",   "/tmp/v2_1mb_diff_p16_len5.avi","/tmp/v2_2mb_diff_p16_len5.avi",+16},
		{"len6_neg32",   "/tmp/v2_1mb_diff_n32_len6.avi","/tmp/v2_2mb_diff_n32_len6.avi",-32},
		{"len7_pos107",  "/tmp/v2_1mb_white.avi",       "/tmp/v2_2mb_white.avi",  +107},
	}
	for _, tc := range cases {
		f1, err := v2FirstFrame(tc.p1MB); if err != nil { t.Logf("%-16s SKIP: %v", tc.name, err); continue }
		f2, err := v2FirstFrame(tc.p2MB); if err != nil { t.Logf("%-16s SKIP2: %v", tc.name, err); continue }
		bs1 := frameToBitString(f1)[12:]
		bs2 := frameToBitString(f2)[12:]
		lcp := 0
		for lcp < len(bs1) && lcp < len(bs2) && bs1[lcp] == bs2[lcp] { lcp++ }
		// MB0 content = lcp bits. Y0 code = bits [6, 6+y0len).
		// After Y0: 5 zero-diff blocks (Y1,Y2,Y3,Cb,Cr).
		// Luma zero: VLC_luma(0)="100" (3 bits each × 3 = 9 bits).
		// Chroma zero: VLC_chroma(0)="00" (2 bits each × 2 = 4 bits).
		// So Y0 code length = lcp - 6 - 9 - 4 = lcp - 19.
		y0Len := lcp - 19
		dc := bs1[6:]
		end := y0Len + 4; if end > len(dc) { end = len(dc) }
		vlcCode := dc[:y0Len]
		// Compute expected u(len) bits for the diff
		diff := tc.diff
		var lenVal, uval int
		if diff == 0 {
			lenVal = 0
		} else {
			abs := diff; if abs < 0 { abs = -abs }
			for (1 << lenVal) <= abs { lenVal++ }
			if diff > 0 { uval = diff } else { uval = (-diff) ^ ((1 << lenVal) - 1) }
		}
		var uBits string
		if lenVal > 0 {
			for b := lenVal - 1; b >= 0; b-- {
				if (uval >> b) & 1 == 1 { uBits += "1" } else { uBits += "0" }
			}
		}
		t.Logf("%-16s LCP=%2d MB0=%2d diff=%4d len=%d  VLC=%q u=%s", tc.name, lcp, lcp, diff, lenVal, vlcCode, uBits)
	}
}

// TestV2ChromaDC extracts chroma DC bits from colored frames.
// For luma DC encoding: VLC(len) + u(len).
// Red: Y≈81 (Cb≈90, Cr≈240), Blue: Y≈29 (Cb≈255, Cr≈107).
func TestV2ChromaDC(t *testing.T) {
	// luma dc2 VLC table (luma, len→bits):
	lumaVLC := map[int]string{0:"100", 1:"00", 2:"01", 3:"101", 4:"110", 5:"1110", 6:"11110", 7:"111110"}
	// dc2_vlc decode: map from bit-string → len
	lumaVLCInv := map[string]int{}
	for l, code := range lumaVLC { lumaVLCInv[code] = l }

	encodeU := func(diff, len int) string {
		var u int
		if diff >= 0 { u = diff } else { u = (-diff) ^ ((1 << len) - 1) }
		s := ""
		for b := len - 1; b >= 0; b-- {
			if (u >> b) & 1 == 1 { s += "1" } else { s += "0" }
		}
		return s
	}

	for _, tc := range []struct {
		name       string
		path1MB    string
		Y, Cb, Cr  int
	}{
		{"red", "/tmp/v2_1mb_red.avi",  81,  90, 240},
		{"blue","/tmp/v2_1mb_blue.avi", 29, 255, 107},
	} {
		frame, err := v2FirstFrame(tc.path1MB)
		if err != nil { t.Logf("%-8s SKIP: %v", tc.name, err); continue }
		bs := frameToBitString(frame)[12:] // MB layer bits
		dc := bs[6:] // skip overhead

		// Decode luma DC: Y0 diff = tc.Y - 128; Y1,Y2,Y3 diff = 0 (pred = tc.Y)
		lumaPos := 0
		diffs := []int{tc.Y - 128, 0, 0, 0}
		lumaLens := make([]int, 4)
		for i, d := range diffs {
			abs := d; if abs < 0 { abs = -abs }
			l := 0; for (1 << l) <= abs && abs > 0 { l++ }
			lumaLens[i] = l
		}
		for i, d := range diffs {
			l := lumaLens[i]
			vlc := lumaVLC[l]
			u := encodeU(d, l)
			_ = vlc; _ = u
			lumaPos += len(vlc) + l
		}
		// chroma DC starts at lumaPos in dc string
		chromaDC := dc[lumaPos:]

		// Compute expected Cb, Cr diffs
		CbDiff := tc.Cb - 128
		CrDiff := tc.Cr - 128
		var CbLen, CrLen int
		absCb := CbDiff; if absCb < 0 { absCb = -absCb }
		absCr := CrDiff; if absCr < 0 { absCr = -absCr }
		for (1 << CbLen) <= absCb && absCb > 0 { CbLen++ }
		for (1 << CrLen) <= absCr && absCr > 0 { CrLen++ }
		CbU := encodeU(CbDiff, CbLen)
		CrU := encodeU(CrDiff, CrLen)

		// Extract VLC_chroma(CbLen) = chromaDC[:x] where chromaDC[x:x+CbLen]==CbU
		// Search for CbU in chromaDC
		chromaStr := chromaDC[:20]; if len(chromaDC) < 20 { chromaStr = chromaDC }
		t.Logf("=== %s === LumaPos=%d chromaDC=%s", tc.name, lumaPos, chromaStr)
		t.Logf("  Cb: diff=%d len=%d u=%s", CbDiff, CbLen, CbU)
		t.Logf("  Cr: diff=%d len=%d u=%s", CrDiff, CrLen, CrU)
		// Find CbU in chromaDC
		for p := 0; p+CbLen <= len(chromaDC); p++ {
			if chromaDC[p:p+CbLen] == CbU {
				t.Logf("  Cb u=%s found at chromaDC pos %d  → VLC_chroma(%d)=%q", CbU, p, CbLen, chromaDC[:p])
				// Then Cr VLC starts at p+CbLen
				crStart := p + CbLen
				if crStart+CrLen <= len(chromaDC) {
					for q := crStart; q+CrLen <= len(chromaDC); q++ {
						if chromaDC[q:q+CrLen] == CrU {
							t.Logf("  Cr u=%s found at chromaDC pos %d  → VLC_chroma(%d)=%q", CrU, q, CrLen, chromaDC[crStart:q])
							break
						}
					}
				}
				break
			}
		}
		_ = lumaVLCInv
	}
}

// TestV2ChromaVLC determines chroma DC VLC codes for small diffs.
// Uses frames with tiny blue component to get Cb diff = 1,2,4,8.
func TestV2ChromaVLC(t *testing.T) {
	lumaVLC := map[int]string{0:"100", 1:"00", 2:"01", 3:"101", 4:"110", 5:"1110", 6:"11110", 7:"111110"}
	encodeU := func(diff, lenVal int) string {
		var u int
		if diff >= 0 { u = diff } else { u = (-diff) ^ ((1 << lenVal) - 1) }
		s := ""
		for b := lenVal - 1; b >= 0; b-- {
			if (u >> b) & 1 == 1 { s += "1" } else { s += "0" }
		}
		return s
	}
	diffLen := func(diff int) int {
		abs := diff; if abs < 0 { abs = -abs }
		l := 0; for (1 << l) <= abs && abs > 0 { l++ }
		return l
	}
	for _, tc := range []struct {
		name string; p1MB, p2MB string
		Y, Cb, Cr int
	}{
		// B=2,5,9,18 → Cb≈129,130,132,136 (diff=1,2,4,8). Y≈16, Cr≈128.
		{"cb_b2",  "/tmp/v2_1mb_cb_b2.avi",  "/tmp/v2_2mb_cb_b2.avi",  16, 129, 128},
		{"cb_b5",  "/tmp/v2_1mb_cb_b5.avi",  "/tmp/v2_2mb_cb_b5.avi",  16, 130, 128},
		{"cb_b9",  "/tmp/v2_1mb_cb_b9.avi",  "/tmp/v2_2mb_cb_b9.avi",  16, 132, 128},
		{"cb_b18", "/tmp/v2_1mb_cb_b18.avi", "/tmp/v2_2mb_cb_b18.avi", 16, 136, 128},
	} {
		f1, err := v2FirstFrame(tc.p1MB); if err != nil { t.Logf("%-12s SKIP: %v", tc.name, err); continue }
		f2, err := v2FirstFrame(tc.p2MB); if err != nil { t.Logf("%-12s SKIP2: %v", tc.name, err); continue }
		bs1 := frameToBitString(f1)[12:]
		bs2 := frameToBitString(f2)[12:]
		lcp := 0
		for lcp < len(bs1) && lcp < len(bs2) && bs1[lcp] == bs2[lcp] { lcp++ }

		// Compute luma DC bit offset (skip overhead 6, then luma blocks)
		yDiff := tc.Y - 128; yLen := diffLen(yDiff)
		yCode := len(lumaVLC[yLen]) + yLen  // Y0 total bits
		lumaPos := yCode + 3*3  // Y0 + 3×len=0 luma = Y0 + 9 bits
		dc := bs1[6:]
		chromaDC := dc[lumaPos:]

		// Cb: diff, len, u
		cbDiff := tc.Cb - 128; cbLen := diffLen(cbDiff); cbU := encodeU(cbDiff, cbLen)
		// Cr: diff=0
		crLen := 0

		// Find cbU in chromaDC
		var cbVLC string
		for p := 0; p+cbLen <= len(chromaDC); p++ {
			if chromaDC[p:p+cbLen] == cbU {
				cbVLC = chromaDC[:p]
				break
			}
		}
		// Cr VLC starts after Cb
		crStart := len(cbVLC) + cbLen
		crVLC := ""
		if crStart < len(chromaDC) { crVLC = chromaDC[crStart:crStart+3]; if crStart+3 > len(chromaDC) { crVLC = chromaDC[crStart:] } }

		t.Logf("%-12s LCP=%2d MB0=%2d diff(Y=%3d,Cb=%3d) VLC_chroma(%d)=%q Cr_start=%s",
			tc.name, lcp, lcp, yDiff, cbDiff, cbLen, cbVLC, crVLC)
		_ = crLen
	}
}

// TestV2ChromaRaw dumps raw chroma DC bits for various frames to resolve the table.
func TestV2ChromaRaw(t *testing.T) {
	// Luma DC VLC for Y=16 (diff=-112, len=7): "111110" + u(7)=0001111 = 13 bits.
	// Then Y1,Y2,Y3 with diff=0: 3×"100" = 9 bits. Total luma = 22 bits.
	// chromaDC starts at bit 22 of DC area (= mb bit 28).
	for _, tc := range []struct{ name, path string; cbDiff int }{
		{"Cb_diff_0",  "/tmp/v2_1mb_gray128.avi",  0},   // Cb=128, diff=0
		{"Cb_diff_p1", "/tmp/v2_1mb_cb_b2.avi",    1},   // Cb≈129, diff=+1
		{"Cb_diff_p2", "/tmp/v2_1mb_cb_b5.avi",    2},   // Cb≈130, diff=+2
		{"Cb_diff_p4", "/tmp/v2_1mb_cb_b9.avi",    4},   // Cb≈132, diff=+4
	} {
		frame, err := v2FirstFrame(tc.path); if err != nil { t.Logf("SKIP %s: %v", tc.name, err); continue }
		bs := frameToBitString(frame)[12:] // MB layer
		dc := bs[6:]  // DC bits (skip overhead)
		
		// Luma DC is 22 bits for Y=16 (diff=-112, len=7) + 3 zeros
		// BUT for gray128 (Y=126, diff=-2, len=2): luma = (2+2) + 3×3 = 13 bits
		// Compute dynamically from MB0 size known from LCP analysis:
		// gray128 MB0=23: dc content = 23-6 = 17 bits. chroma = 17-13 = 4 bits.
		// cb_b2,b5,b9: Y=16, lumaLen = (6+7)+9 = 22 bits
		var lumaLen int
		if tc.cbDiff == 0 {
			lumaLen = 13 // gray128: Y diff=-2 (len=2: 4 bits) + 3×3 = 4+9 = 13
		} else {
			lumaLen = 22 // blue-tinted: Y diff=-112 (len=7: 13 bits) + 9 = 22
		}
		chromaDC := dc[lumaLen:]
		end := 12; if end > len(chromaDC) { end = len(chromaDC) }
		t.Logf("%-16s cbDiff=%2d chromaDC=%s (lumaLen=%d)", tc.name, tc.cbDiff, chromaDC[:end], lumaLen)
	}
}

// TestV2ChromaFull dumps luma+chroma DC bits to verify lumaLen.
func TestV2ChromaFull(t *testing.T) {
	for _, tc := range []struct{ name, path string }{
		{"b9",  "/tmp/v2_1mb_cb_b9.avi"},
		{"b18", "/tmp/v2_1mb_cb_b18.avi"},
	} {
		frame, err := v2FirstFrame(tc.path); if err != nil { t.Logf("SKIP: %v", err); continue }
		bs := frameToBitString(frame)[12:] // after 12-bit header
		dc := bs[6:]  // after 6-bit overhead
		t.Logf("%s: dc=%s", tc.name, dc[:min2(40, len(dc))])
	}
}

func min2(a, b int) int { if a < b { return a }; return b }

// TestDecodeV2 decodes a v2 frame and checks that the result is close to the expected color.
func TestDecodeV2(t *testing.T) {
	for _, tc := range []struct {
		name     string
		path     string
		wantY, wantCb, wantCr int
	}{
		{"gray128",   "/tmp/v2_1mb_gray128.avi",    126, 128, 128},
		{"red",       "/tmp/v2_red.avi",              81,  90, 240},
		{"cb_b18",    "/tmp/v2_1mb_cb_b18.avi",       18, 136, 127},
	} {
		f, err := v2FirstFrame(tc.path)
		if err != nil { t.Logf("%s: SKIP %v", tc.name, err); continue }
		img, err := DecodeIntraFrameV2(f, 16, 16)
		if err != nil { t.Errorf("%s: decode error: %v", tc.name, err); continue }
		y := int(img.Y[0])
		cb := int(img.Cb[0])
		cr := int(img.Cr[0])
		ok := abs(y-tc.wantY) <= 4 && abs(cb-tc.wantCb) <= 4 && abs(cr-tc.wantCr) <= 4
		t.Logf("%s: Y=%d Cb=%d Cr=%d (want Y≈%d Cb≈%d Cr≈%d) %v",
			tc.name, y, cb, cr, tc.wantY, tc.wantCb, tc.wantCr, map[bool]string{true: "OK", false: "BAD"}[ok])
		if !ok {
			t.Errorf("%s: pixel mismatch", tc.name)
		}
	}
}

func TestDecodeAVIFirstFrameV2(t *testing.T) {
	f, err := os.Open("/tmp/v2_test_32x32.avi")
	if err != nil { t.Skip("no test file:", err); return }
	defer f.Close()
	img, err := DecodeAVIFirstFrame(f)
	if err != nil { skipIfNoFixture(t, err); t.Fatalf("DecodeAVIFirstFrame: %v", err); return }
	t.Logf("size=%dx%d", img.Bounds().Dx(), img.Bounds().Dy())
}
