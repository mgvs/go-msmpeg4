package msmpeg4

type vlcKey struct{ length, code int }

// Per-MB prefix → luma coded-block pattern (which of the 4 luma blocks carry AC),
// for cbpc=00. Reverse-engineered (re/NOTES.md). The prefix bundles MCBPC + an
// ac_pred-related field + CBPY; here we only need the resulting 4-bit luma pattern.
// {code length, code bits} → [4]cbp bits (block 0..3).
type cbpyEntry struct {
	length, code   int
	b0, b1, b2, b3 int
}

var cbpyTable = []cbpyEntry{
	{2, 0b10, 0, 0, 0, 0},
	{6, 0b001100, 0, 0, 0, 1},
	{7, 0b0000100, 0, 0, 1, 0},
	{6, 0b000100, 0, 0, 1, 1},
	{7, 0b0000010, 0, 1, 0, 0},
	{6, 0b000110, 0, 1, 0, 1},
	{8, 0b01000100, 0, 1, 1, 0},
	{7, 0b0111100, 0, 1, 1, 1},
	{7, 0b0101010, 1, 0, 0, 0},
	{9, 0b000011010, 1, 0, 0, 1},
	{7, 0b0101000, 1, 0, 1, 0},
	{8, 0b00001110, 1, 0, 1, 1},
	{7, 0b0111110, 1, 1, 0, 0},
	{8, 0b00000010, 1, 1, 0, 1},
	{8, 0b00100100, 1, 1, 1, 0},
	{5, 0b01100, 1, 1, 1, 1},
}

var cbpyMap = func() map[vlcKey][4]int {
	m := make(map[vlcKey][4]int, len(cbpyTable))
	for _, e := range cbpyTable {
		m[vlcKey{e.length, e.code}] = [4]int{e.b0, e.b1, e.b2, e.b3}
	}
	return m
}()

// decodeCBPY reads the per-MB prefix and returns the 4-bit luma AC pattern.
func (r *bitReader) decodeCBPY() ([4]int, bool) {
	code, n := 0, 0
	for n < 9 {
		code = code<<1 | r.bit()
		n++
		if p, ok := cbpyMap[vlcKey{n, code}]; ok {
			return p, true
		}
	}
	return [4]int{}, false
}
