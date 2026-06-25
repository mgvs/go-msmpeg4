"""Derive MS-MPEG4 v3 DC differential VLCs (dc_table_index 0/1, luma+chroma) by decoding
real DivX3 files with an instrumented ffmpeg. ffmpeg directly outputs each DC code's
bit string and magnitude — no frame-sync or bitstream slicing needed."""
import os
import subprocess, os, re, json, collections

FF = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "..", "ffmpeg", "ffmpeg")

DIV3_FILES = [
    os.path.expanduser("~/Movies/tests/movie4.avi"),
    os.path.expanduser("~/Movies/tests/movie1.avi"),
    os.path.expanduser("~/Movies/tests/movie2.avi"),
    os.path.expanduser("~/Movies/tests/movie3.avi"),
    os.path.expanduser("~/Movies/tests/movie5.avi"),
    os.path.expanduser("~/Movies/tests/movie6.avi"),
    os.path.expanduser("~/Movies/tests/movie7.avi"),
    os.path.expanduser("~/Movies/tests/movie8.avi"),
    os.path.expanduser("~/Movies/tests/movie9.avi"),
]

FRAMES_PER_FILE = 800


def decode(path, n=FRAMES_PER_FILE):
    env = dict(os.environ, DCINSTR="1")
    r = subprocess.run(
        [FF, "-i", path, "-frames:v", str(n), "-f", "rawvideo",
         "-pix_fmt", "yuv420p", "-y", "/tmp/o.raw"],
        env=env, capture_output=True, timeout=300,
    )
    out = []
    for l in r.stderr.decode("latin1").splitlines():
        m = re.match(r"DCVLC dct=(\d) ch=(\d) code=([01]+) mag=(-?\d+)", l)
        if m:
            dct, ch, code, mag = int(m[1]), int(m[2]), m[3], int(m[4])
            out.append((dct, ch, code, mag))
    return out


# (dct, ch) -> code -> mag
tables = {}
conflicts = 0

for path in DIV3_FILES:
    print(f"  {os.path.basename(path)}...", flush=True)
    entries = decode(path)
    for dct, ch, code, mag in entries:
        key = (dct, ch)
        if key not in tables:
            tables[key] = {}
        prev = tables[key].get(code)
        if prev is not None and prev != mag:
            print(f"    CONFLICT dct={dct} ch={ch} code={code!r} was={prev} new={mag}")
            conflicts += 1
        else:
            tables[key][code] = mag
    by_key = collections.Counter((dct, ch) for dct, ch, _, _ in entries)
    print(f"    → {len(entries)} entries: {dict(sorted(by_key.items()))}", flush=True)

print(f"\nTotal conflicts: {conflicts}")
print("\nFinal tables:")
for key in sorted(tables):
    t = tables[key]
    codes = list(t.keys())
    prefix_coll = sum(1 for a in codes for c in codes if a != c and c.startswith(a))
    mags = sorted(set(t.values()))
    missing = [i for i in range(max(mags) + 1) if i not in set(mags)] if mags else []
    print(f"  dct={key[0]} ch={key[1]}: {len(t)} codes, prefix-coll={prefix_coll}, "
          f"max_mag={max(mags) if mags else -1}, missing={missing}")

# Save: mag -> code for each (dct, ch)
out = {}
for (dct, ch), t in tables.items():
    # invert: mag -> code (unique by construction if no conflicts)
    inv = {}
    for code, mag in t.items():
        inv[mag] = code
    out[f"{dct},{ch}"] = inv

json.dump(out, open("/tmp/dc_instr.json", "w"), indent=2)
print("\nSaved /tmp/dc_instr.json")
