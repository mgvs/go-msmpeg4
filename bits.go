// Package msmpeg4 is a clean-room pure-Go decoder for the Microsoft MPEG-4
// family (v1/v2/v3, FourCC DIV3/MP42/…). Tables are derived from the open
// H.263 / MPEG-4 Part 2 standards plus deviation rules established by
// black-box bitstream analysis — no Microsoft binary, no third-party source.
package msmpeg4

// bitReader reads bits MSB-first (the H.263 / MPEG-4 convention).
type bitReader struct {
	data []byte
	pos  int // bit position
}

func newBitReader(b []byte) *bitReader { return &bitReader{data: b} }

func (r *bitReader) pos1() int    { return r.pos }
func (r *bitReader) left() int    { return len(r.data)*8 - r.pos }
func (r *bitReader) eof() bool    { return r.pos >= len(r.data)*8 }
func (r *bitReader) seek(bit int) { r.pos = bit }

// bit reads one bit (0 past EOF).
func (r *bitReader) bit() int {
	if r.pos >= len(r.data)*8 {
		r.pos++
		return 0
	}
	b := int(r.data[r.pos>>3]>>(7-uint(r.pos&7))) & 1
	r.pos++
	return b
}

// u reads n bits MSB-first as an unsigned value.
func (r *bitReader) u(n int) int {
	v := 0
	for i := 0; i < n; i++ {
		v = v<<1 | r.bit()
	}
	return v
}

// peek returns the next n bits without consuming.
func (r *bitReader) peek(n int) int {
	save := r.pos
	v := r.u(n)
	r.pos = save
	return v
}

// show returns the next n bits as a "0101" string (for RE inspection).
func (r *bitReader) show(n int) string {
	save := r.pos
	buf := make([]byte, n)
	for i := 0; i < n; i++ {
		buf[i] = byte('0' + r.bit())
	}
	r.pos = save
	return string(buf)
}
