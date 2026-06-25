package msmpeg4

import (
	"encoding/binary"
	"os"
	"testing"
)

func Test6daysPFrameTrace(t *testing.T) {
	avi := "testdata/movie4.avi"
	data, err := os.ReadFile(avi)
	if err != nil {
		t.Skip("no file")
	}
	moviIdx := -1
	for i := 0; i+4 <= len(data); i++ {
		if string(data[i:i+4]) == "movi" {
			moviIdx = i
			break
		}
	}
	if moviIdx < 0 {
		t.Fatal("no movi")
	}
	var frames [][]byte
	p := moviIdx + 4
	for p+8 <= len(data) && len(frames) < 3 {
		sz := int(binary.LittleEndian.Uint32(data[p+4:]))
		fc := string(data[p : p+4])
		if (fc == "00dc" || fc == "00db") && sz > 0 {
			frames = append(frames, data[p+8:p+8+sz])
		}
		p += 8 + sz + (sz & 1)
	}
	if len(frames) < 2 {
		t.Fatal("not enough frames")
	}
	pFrame := frames[1]

	r := newBitReader(pFrame)
	r.u(2)      // pictype
	q := r.u(5) // quantizer
	useMBSkip := r.bit() == 1
	rcIdx := r.c3()
	dcIdx := r.bit()
	mvIdx := r.bit()
	t.Logf("Header: q=%d useMBSkip=%v rcIdx=%d dcIdx=%d mvIdx=%d pos=%d", q, useMBSkip, rcIdx, dcIdx, mvIdx, r.pos)

	const w, h = 576, 256
	mbw := w / 16
	type mv2 struct{ x, y int }
	mvGrid := make([]mv2, mbw*(h/16))

	for my := 0; my < h/16; my++ {
		for mx := 0; mx < mbw; mx++ {
			startPos := r.pos
			if useMBSkip && r.bit() == 1 {
				mvGrid[my*mbw+mx] = mv2{0, 0}
				if my < 2 {
					t.Logf("MB(%d,%d): SKIP pos=%d->%d", mx, my, startPos, r.pos)
				}
				continue
			}

			intraMB, cbp, ok := r.decodeMBNonIntra()
			if !ok {
				t.Fatalf("MB(%d,%d) mbNonIntra FAIL at pos=%d bits=%s", mx, my, startPos, r.show(8))
			}
			afterVLC := r.pos

			var mvx, mvy int
			if !intraMB {
				dmvx, dmvy, ok := r.decodeMVVLC(mvIdx)
				if !ok {
					t.Fatalf("MB(%d,%d) MV FAIL at pos=%d", mx, my, afterVLC)
				}
				var plx, ply, ptx, pty, prx, pry int
				if mx > 0 {
					plx = mvGrid[my*mbw+mx-1].x
					ply = mvGrid[my*mbw+mx-1].y
				}
				if my > 0 {
					ptx = mvGrid[(my-1)*mbw+mx].x
					pty = mvGrid[(my-1)*mbw+mx].y
				}
				if my > 0 && mx < mbw-1 {
					prx = mvGrid[(my-1)*mbw+mx+1].x
					pry = mvGrid[(my-1)*mbw+mx+1].y
				} else if my > 0 {
					prx = ptx
					pry = pty
				}
				mvx = mvMedian3(plx, ptx, prx) + dmvx
				mvy = mvMedian3(ply, pty, pry) + dmvy
				mvGrid[my*mbw+mx] = mv2{mvx, mvy}

				if my < 2 {
					t.Logf("MB(%d,%d): inter cbp=%d(0b%06b) dmv=(%d,%d) mv=(%d,%d) vlcBits=%d-(%d) mvBits=%d->%d",
						mx, my, cbp, cbp, dmvx, dmvy, mvx, mvy,
						startPos, afterVLC, afterVLC, r.pos)
				}

				// Consume coded blocks.
				tcSet := chromaTCOEF[rcIdx]
				for blk := 0; blk < 6; blk++ {
					if (cbp>>(5-blk))&1 == 0 {
						continue
					}
					blkStart := r.pos
					for n := 0; ; n++ {
						if n >= 64 {
							t.Fatalf("MB(%d,%d) blk%d TCOEF infinite loop", mx, my, blk)
						}
						c, ok := r.decodeTCOEF(tcSet, 0)
						if !ok {
							t.Fatalf("MB(%d,%d) blk%d TCOEF FAIL at pos=%d", mx, my, blk, r.pos)
						}
						if c.last {
							break
						}
					}
					if my < 2 {
						t.Logf("  blk%d: tcoef bits %d->%d", blk, blkStart, r.pos)
					}
				}
			} else {
				// Intra MB in P-frame: read acPred + 6 blocks.
				mvGrid[my*mbw+mx] = mv2{0, 0}
				r.bit() // acPred
				dcLuma := dcTables[dcIdx][0]
				dcChro := dcTables[dcIdx][1]
				lumaSet := lumaTCOEF[rcIdx]
				for blk := 0; blk < 6; blk++ {
					if blk < 4 {
						dcLuma.decode(r)
					} else {
						dcChro.decode(r)
					}
					if cbp != 0 && (cbp>>(5-blk))&1 == 1 {
						ts := chromaTCOEF[rcIdx]
						if blk < 4 {
							ts = lumaSet
						}
						for n := 0; ; n++ {
							if n >= 64 {
								t.Fatalf("MB(%d,%d) intra blk%d TCOEF loop", mx, my, blk)
							}
							c, ok := r.decodeTCOEF(ts, 0)
							if !ok {
								t.Fatalf("MB(%d,%d) intra blk%d TCOEF FAIL", mx, my, blk)
							}
							if c.last {
								break
							}
						}
					}
				}
				if my < 2 {
					t.Logf("MB(%d,%d): INTRA cbp=%d pos=%d->%d", mx, my, cbp, startPos, r.pos)
				}
			}
		}
	}
	t.Logf("Done. Final pos=%d frame_bits=%d", r.pos, len(pFrame)*8)
}

func Test6daysMB21Block0(t *testing.T) {
	avi := "testdata/movie4.avi"
	frames := videoFrames(t, avi, 5)
	if len(frames) < 2 {
		t.Skip("not enough frames")
	}
	pFrame := func() []byte {
		for _, f := range frames {
			if FrameType(f) == picInter {
				return f
			}
		}
		return nil
	}()
	if pFrame == nil {
		t.Skip("no P-frame")
	}

	r := newBitReader(pFrame)
	r.u(2)
	r.u(5)
	r.bit()
	r.c3()
	r.bit()
	r.bit()
	// Skip to bit 1396 (TCOEF for MB(21,0) blk0).
	for r.pos < 1396 {
		r.bit()
	}
	t.Logf("bit pos before blk0 TCOEF: %d", r.pos)

	// rcIdx=2 → chromaTCOEF[2]
	tcSet := chromaTCOEF[2]
	coeff, ok := r.decodeInterBlock(12, tcSet, 0)
	t.Logf("decodeInterBlock ok=%v pos_after=%d", ok, r.pos)
	t.Logf("non-zero coeffs:")
	for i, v := range coeff {
		if v != 0 {
			t.Logf("  [%d] (r=%d c=%d) = %.0f", i, i/8, i%8, v)
		}
	}
	residual := idct8(&coeff)
	t.Logf("IDCT residual row-by-row:")
	for i := 0; i < 8; i++ {
		row := make([]int, 8)
		for j := 0; j < 8; j++ {
			row[j] = int(residual[i*8+j])
		}
		t.Logf("  row%d: %v", i, row)
	}
}

func Test6daysMB21Block0Verbose(t *testing.T) {
	avi := "testdata/movie4.avi"
	frames := videoFrames(t, avi, 5)
	if len(frames) < 2 {
		t.Skip("not enough frames")
	}
	pFrame := func() []byte {
		for _, f := range frames {
			if FrameType(f) == picInter {
				return f
			}
		}
		return nil
	}()
	if pFrame == nil {
		t.Skip("no P-frame")
	}

	r := newBitReader(pFrame)
	r.u(2)
	r.u(5)
	r.bit()
	r.c3()
	r.bit()
	r.bit()
	for r.pos < 1396 {
		r.bit()
	}

	tcSet := chromaTCOEF[2]
	pos := 0
	for n := 0; n < 20; n++ {
		startPos := r.pos
		c, ok := r.decodeTCOEF(tcSet, 0)
		if !ok {
			t.Fatalf("TCOEF decode failed at pos=%d", startPos)
		}
		scanPos := pos + c.run
		var rasterPos int
		if scanPos < 64 {
			rasterPos = scanZigzag[scanPos]
		}
		t.Logf("coeff%d: bits[%d:%d] run=%d level=%d last=%v → scanPos=%d raster=%d (r=%d c=%d)",
			n, startPos, r.pos, c.run, c.level, c.last, scanPos, rasterPos, rasterPos/8, rasterPos%8)
		pos += c.run + 1
		if c.last {
			break
		}
	}
}
