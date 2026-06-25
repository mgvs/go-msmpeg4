package msmpeg4

// AC coefficient (TCOEF) decoding for MS-MPEG4 v3 intra blocks.
//
// A coefficient is (last, run, level): `run` zeros precede it in scan order, `level`
// is its quantised magnitude (a sign bit follows the VLC), `last` ends the block.
// Coding is a direct RL-VLC for common (run,level,last) triples, else an escape:
//   mode 1 (esc + "1"):  base code, level += maxlev[last][run]   (level escape)
//   mode 2 (esc + "01"): base code returned as-is                (run escape, see note)
//   mode 3 (esc + "00"): explicit last(1) + run(6) + level(8, two's-complement signed)
//
// The picture header selects which RL table is used (rl_table_index for luma,
// rl_chroma_table_index for chroma); each table has its own escape codeword. Tables
// are registered by index in lumaTCOEF / chromaTCOEF below.
//
// Note: mode 2 (run escape) currently returns the base code without a run offset,
// mirroring the validated Python reference (re/clean_recon.py); config-0 keyframes do
// not exercise it. See re/NOTES.md.

type tcoefKey struct{ length, code int }

// tcoefTableSet is one RL table plus its escape codeword and level-escape maxima.
type tcoefTableSet struct {
	m      map[tcoefKey]tcoefCode
	esc    int // escape codeword value
	escLen int // escape codeword bit length
	maxlev *[2][64]int
	maxrun *[2][64]int
}

func buildTcoefSet(t []tcoefCode, esc, escLen int, maxlev *[2][64]int) *tcoefTableSet {
	m := make(map[tcoefKey]tcoefCode, len(t))
	for _, e := range t {
		m[tcoefKey{e.length, e.code}] = e
	}
	var mr [2][64]int
	for _, e := range t {
		if e.level >= 0 && e.level < 64 && e.run > mr[e.last][e.level] {
			mr[e.last][e.level] = e.run
		}
	}
	return &tcoefTableSet{m: m, esc: esc, escLen: escLen, maxlev: maxlev, maxrun: &mr}
}

// lumaTCOEF maps the header's rl_table_index to a luma RL table.
var lumaTCOEF = map[int]*tcoefTableSet{
	0: buildTcoefSet(tcoefTable0VLC, 0b0010110, 7, &maxlevTable0),
	1: buildTcoefSet(tcoefTable2VLC, 0b001001010, 9, &maxlevTable2),
	2: buildTcoefSet(tcoefLumaVLC, 0b0000011, 7, &maxlevLuma),
}

// chromaTCOEF maps rl_chroma_table_index to a chroma RL table.
var chromaTCOEF = map[int]*tcoefTableSet{
	0: buildTcoefSet(tcoefTable1VLC, 0b000001101, 9, &maxlevTable1),
	1: buildTcoefSet(tcoefChromaVLC, 0b101101001, 9, &maxlevChroma),
	2: buildTcoefSet(tcoefInterVLC, 0b0000011, 7, &maxlevInter),
}

const tcoefMaxLen = 16

// acCoeff is one decoded AC coefficient.
type acCoeff struct {
	run, level int
	last       bool
}

// decodeTCOEF reads one AC coefficient (run, signed level, last) using table set t.
// ok=false on a malformed stream.
func (r *bitReader) decodeTCOEF(t *tcoefTableSet) (acCoeff, bool) {
	matchDirect := func() (tcoefCode, bool) {
		code, n := 0, 0
		for n < tcoefMaxLen {
			code = code<<1 | r.bit()
			n++
			if e, ok := t.m[tcoefKey{n, code}]; ok {
				return e, true
			}
		}
		return tcoefCode{}, false
	}

	if r.peek(t.escLen) == t.esc {
		r.u(t.escLen)
		switch {
		case r.bit() == 1: // mode 1: level escape
			e, ok := matchDirect()
			if !ok {
				return acCoeff{}, false
			}
			level := e.level + (*t.maxlev)[e.last][e.run]
			if r.bit() == 1 {
				level = -level
			}
			return acCoeff{run: e.run, level: level, last: e.last == 1}, true
		case r.bit() == 1: // mode 2: run escape (run += max-run for this level/last)
			e, ok := matchDirect()
			if !ok {
				return acCoeff{}, false
			}
			lvl := e.level
			if lvl > 63 {
				lvl = 63
			}
			run := e.run + (*t.maxrun)[e.last][lvl]
			level := e.level
			if r.bit() == 1 {
				level = -level
			}
			return acCoeff{run: run, level: level, last: e.last == 1}, true
		default: // mode 3: explicit last(1) + run(6) + level(8, signed)
			last := r.bit()
			run := r.u(6)
			level := r.u(8)
			if level >= 128 {
				level -= 256
			}
			return acCoeff{run: run, level: level, last: last == 1}, true
		}
	}

	// Direct RL-VLC + sign bit.
	e, ok := matchDirect()
	if !ok {
		return acCoeff{}, false
	}
	level := e.level
	if r.bit() == 1 { // sign: 1 = negative
		level = -level
	}
	return acCoeff{run: e.run, level: level, last: e.last == 1}, true
}
