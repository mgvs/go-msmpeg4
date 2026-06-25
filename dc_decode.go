package msmpeg4

// decode reads one DC differential value using this table.
// Returns the signed level and whether decoding succeeded.
func (t *dcTable) decode(r *bitReader) (int, bool) {
	acc := ""
	for range 30 {
		if r.bit() == 1 {
			acc += "1"
		} else {
			acc += "0"
		}
		mag, ok := t.vlc[acc]
		if !ok {
			continue
		}
		if mag == dcMax {
			// escape: 8-bit unsigned magnitude + sign bit
			val := r.u(8)
			if r.bit() == 1 {
				val = -val
			}
			return val, true
		}
		if mag == 0 {
			return 0, true
		}
		// non-zero: sign bit follows
		if r.bit() == 1 {
			return -mag, true
		}
		return mag, true
	}
	return 0, false
}
