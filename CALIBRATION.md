# DPP3 → DPP4 Recipe Calibration Cheat-Sheet

The converter (`dpp3_dpp4_bridge.py`) knows *which* DPP4 tag each DPP3 setting maps
to. What it can't know without your machine is *how the value is encoded* —
ranges, signs, fixed-point scaling, and enum codes differ between versions.

This sheet walks you through building that conversion table **once**. After
that, `convert` runs across your whole library unattended.

> ⚠️ Work on THROW-AWAY COPIES only. Never your originals.
> Requires: `brew install exiftool`, and both DPP3 + DPP4 installed.

> This is experimental, non-production recovery tooling. Use it only for
> calibrated personal-archive recovery on backed-up copies, and verify the
> resulting files in DPP4 before trusting them.

---

## The method (why it works)

You take **one** image and create **two** edited copies of it:

- `cal_dpp3.CR2` — edited *only* in DPP3, each slider set to a known value.
- `cal_dpp4.CR2` — the same edit re-created *by hand* in DPP4.

Then:

```bash
python3 dpp3_dpp4_bridge.py calibrate --dpp3 cal_dpp3.CR2 --dpp4 cal_dpp4.CR2
```

It prints the DPP3 value and the DPP4 value **side by side** for each mapped
setting. You read off the relationship and encode it as the `xform` in
`TAGMAP`. Three relationships cover almost everything:

| What you see | Meaning | `xform` to write |
|---|---|---|
| DPP3 `2.00` → DPP4 `2.00` | identical | `identity` (default) |
| DPP3 `100` → DPP4 `10` | scaled ×10 | `lambda v: float(v)/10` |
| DPP3 `4` → DPP4 `-4` | sign flip | `lambda v: -int(v)` |
| DPP3 `Daylight` → DPP4 `4` | enum remap | dict lookup (below) |

---

## Set these EXACT values in DPP3 (so the diff is unambiguous)

Pick deliberately *asymmetric, non-zero* numbers — never 0 or a midpoint —
so you can tell scaling and sign apart at a glance. Suggested settings:

| Setting | Set in DPP3 to | Why this value |
|---|---|---|
| Brightness | **+1.50** | already verified 1:1, use as a sanity anchor |
| Contrast | **+3** | small + positive → reveals sign & range |
| Saturation | **−2** | negative → reveals sign convention |
| Color tone / hue | **+4** | reveals hue units (steps vs degrees) |
| Sharpness | **7** | reveals 0–10 vs 0–500 USM scaling |
| White balance | **Daylight** (preset) | reveals the enum code |
| Color temp | **6300 K** | reveals K vs index encoding |
| Picture Style | **Landscape** | reveals style enum codes |
| Luminance NR | **6** | reveals 0–20 scaling |
| Chrominance NR | **9** | reveals 0–20 scaling |
| Crop | left=**684**, top=**726**, w=**3417**, h=**2278** | odd numbers → unmistakable in the diff |
| Tone curve | one **lifted midpoint** (gentle S) | reveals point array encoding |

Save the recipe (in DPP3, "Save" or "Yes to all" on exit). Then recreate the
*same visual intent* in DPP4 on the second copy and save there too.

---

## Reading the output → filling in TAGMAP

Example calibrate row:

```
0x0115   3                    |  0x20303  6     [ContrastAdj]
```

DPP3 contrast `3` shows in DPP4 as `6` → scaled ×2. Edit `dpp3_dpp4_bridge.py`:

```python
0x0115: {"dpp4_id": 0x20303, "name": "ContrastAdj", "xform": lambda v: int(v)*2},
```

### Enum settings (white balance, picture style)

These need a lookup, not arithmetic. From the calibrate row you learn one pair;
repeat the calibration with a couple more presets to fill the dict, then:

```python
WB_MODE = {"Auto": 0, "Daylight": 4, "Cloudy": 5, "Tungsten": 3, "Shade": 6}
0x0018: {"dpp4_id": 0x20101, "name": "WhiteBalanceAdj",
         "xform": lambda v: WB_MODE.get(v.strip(), 0)},
```

(DPP4 also has "Shot Settings" = 255 — map DPP3 "As Shot" to that.)

### Crop struct

Confirm the pixel coords are 1:1 (`CropX` should equal your DPP3 `684`), but do
not batch-write crop yet. DPP4 stores crop in a nested `CropInfo` table:
`CropX`, `CropY`, `CropWidth`, `CropHeight`, `CropRotation`, and friends are
writable child fields, while the top-level `CropInfo` container is not a direct
assignment target in ExifTool 13.55. The converter reports crop and skips it
until we verify the correct child-field write syntax against a real DPP4-bearing
CR2/DR4 calibration file.

### Tone curve (do last; it's the fiddly one)

Dump the raw arrays from both files and compare directly:

```bash
exiftool -CanonVRD:LuminanceCurvePoints cal_dpp3.CR2
exiftool -CanonVRD:ToneCurve cal_dpp4.CR2
```

Figure out: how many points DPP4 keeps, whether values are 0–255 or 0–4095,
and the field order inside the `ToneCurve` struct. Do not batch-write tone
curves yet. Like crop, DPP4 stores tone curves in a nested table; the converter
reports them with `--enable-tonecurve` and skips the write until the child-field
write syntax is verified. A wrong curve is worse than none.

---

## Verify each tag before trusting the batch

1. `python3 dpp3_dpp4_bridge.py convert cal_dpp3.CR2 --dry-run` → check the plan.
2. Run for real (it writes to a copy), then **open that copy in DPP4**.
3. Compare against `cal_dpp4.CR2`. Values should match the sliders you set.
4. Only once a tag verifies, trust it across your library.

## Then run the library

```bash
# dry-run a whole folder first
for f in /path/to/raws/*.CR2; do
  python3 dpp3_dpp4_bridge.py convert "$f" --dry-run
done

# apply (each writes a *.dpp4copy.CR2 alongside the original)
for f in /path/to/raws/*.CR2; do
  python3 dpp3_dpp4_bridge.py convert "$f"
done
```

Spot-check a dozen in DPP4. Remember: this restores edit *intent*, not a
pixel-perfect match — DPP4's engine renders differently (usually better) than
DPP3 did.
