# dpp3-dpp4-bridge

Migrate Canon **Digital Photo Professional 3** edit recipes into a form
**DPP4** can read — recovering years of DPP3 edits that DPP4 otherwise ignores.

> ## ⚠️ UNOFFICIAL — not affiliated with, endorsed, or supported by Canon
> This is an independent, community-made tool. It is **not** a Canon product and
> Canon provides **no** support for it. "Canon", "DPP", and "Digital Photo
> Professional" are trademarks of Canon Inc., used here only for identification.
> The tool works by writing into Canon's undocumented `CanonVRD` recipe data;
> that format is reverse-engineered and may change or behave unexpectedly.
>
> **Provided AS-IS, with no warranty of any kind. Use entirely at your own
> risk.** Always run against **throw-away copies**, never your originals.
> Back up your files first. This recovers your edit *intent*; it is not a
> pixel-perfect match (DPP4's render engine differs from DPP3's).
> See [limitations](#limitations) and the [LICENSE](LICENSE).

## Why this exists

Canon's DPP stores its edit "recipe" inside the CR2 as a `CanonVRD` EXIF
trailer. DPP3 and DPP4 **both** write there — but under **different tag IDs**
(e.g. brightness is `0x0038` in DPP3, `0x20001` in DPP4). DPP4 simply ignores
the DPP3 tags, so opening a DPP3-edited file in DPP4 shows no edits. There has
never been an official converter.

The fix: read each DPP3 tag, map its ID to the DPP4 equivalent, convert the
value into DPP4's encoding, and write it back as a DPP4 tag. exiftool can write
a tag *by ID*, which sidesteps the duplicate-name problem:

```bash
exiftool -CanonVRD:ID-0x20001:RawBrightnessAdj=2.00 file.CR2
```

This tool automates that across a whole library.

## Requirements

- [exiftool](https://exiftool.org/) — `brew install exiftool`
- Python 3.8+
- For calibration: DPP3 **and** DPP4 installed (to confirm value encodings)

## Quickstart

```bash
# 1. See the recipe tags in a file, with DPP3-mappable ones flagged
python3 dpp3_dpp4_bridge.py inspect _MG_2280.CR2

# 2. Preview the conversion (writes nothing)
python3 dpp3_dpp4_bridge.py convert _MG_2280.CR2 --dry-run

# 3. Convert (works on a *copy* by default; originals untouched)
python3 dpp3_dpp4_bridge.py convert _MG_2280.CR2
```

Open the resulting `*.dpp4copy.CR2` in DPP4 to confirm the edits appear.

### Calibrate first (important)

The tag *map* is built in. The value *encodings* (ranges, signs, scaling, enum
codes) differ per setting and must be confirmed on your machine. The `calibrate`
command diffs a DPP3-edited and a DPP4-edited copy of the same image so you can
read off each conversion. Full walkthrough: **[CALIBRATION.md](CALIBRATION.md)**.

```bash
python3 dpp3_dpp4_bridge.py calibrate --dpp3 cal_dpp3.CR2 --dpp4 cal_dpp4.CR2
```

## What's handled

- Scalar settings: brightness (verified 1:1), white balance, color temp,
  picture style, contrast, saturation, hue, sharpness, luminance/chroma NR.
- **Crop** — assembled into DPP4's `CropInfo` struct.
- **Tone curve** — experimental, behind `--enable-tonecurve`; calibrate the
  point encoding before trusting it.

## Limitations

1. **Approximate, not identical.** DPP4 renders differently than DPP3, so even a
   perfect value copy won't look pixel-identical. This restores edit intent.
2. **Value encodings need calibration** on your machine (the `calibrate`
   command makes this mechanical).
3. **Structs** (crop, tone curve) are the least-certain paths — verify them
   against a known calibration image before batch-running.

If you want the *exact* old look with zero risk, the alternative is to batch
export from DPP3 to 16-bit TIFF (baking edits into pixels), then import the
TIFFs into DPP4.

## Credit

The mechanism this tool automates was **reverse-engineered by forum user
[andre-7d](https://community.usa.canon.com/t5/Camera-Software/Does-anyone-know-if-there-is-a-tool-available-to-convert-DPP3/m-p/397975)**
on the Canon Community forums — the differing tag IDs, writing DPP4 tags by ID
via exiftool, and re-applying DPP3 values so DPP4 reads them. Forum user
**Newton** separately confirmed there is no official converter and documented
that the DPP3 recipe stays embedded in the file (DPP4 just ignores it). This
project finishes what andre-7d started: the tag map plus the value-conversion
calibration he never automated.

Tag definitions come from Phil Harvey's
[exiftool CanonVRD tables](https://exiftool.org/TagNames/CanonVRD.html).

## License

MIT — see [LICENSE](LICENSE).
