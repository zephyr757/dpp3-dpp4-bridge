#!/usr/bin/env python3
"""
dpp3-dpp4-bridge  (dpp3_dpp4_bridge.py)
Migrate Canon DPP3 recipes into DPP4-readable recipes.

Credit
------
The entire mechanism this tool automates was reverse-engineered by forum user
*andre-7d* on the Canon Community forums: that DPP3 and DPP4 both write into the
CanonVRD trailer under DIFFERENT tag IDs (e.g. brightness 0x0038 vs 0x20001),
that exiftool can write a DPP4 tag by ID, and that DPP3 values can therefore be
re-applied so DPP4 reads them. See:
  https://community.usa.canon.com/t5/Camera-Software/Does-anyone-know-if-there-is-a-tool-available-to-convert-DPP3/m-p/397975
Forum user *Newton* separately confirmed the dead-end (no official converter)
and the detail that the DPP3 recipe stays embedded in the file -- DPP4 just
ignores it. This script finishes the job andre-7d started: the tag map + the
value-conversion calibration he never automated.

Background
----------
Canon Digital Photo Professional stores its edit "recipe" inside the CR2 file
as a CanonVRD EXIF trailer. DPP3 and DPP4 BOTH write into CanonVRD, but:

  * DPP3 uses one set of tag IDs  (the "Ver1/Ver2 / VRD" tables)
  * DPP4 uses a DIFFERENT set     (the "DR4" tables, IDs like 0x2xxxx / 0xf0100)

DPP4 simply ignores the DPP3 tags. The DPP3 recipe is still physically in the
file (you can see it in a hex/text editor) -- DPP4 just doesn't read it.

So "conversion" is really: read each DPP3 tag, look up the equivalent DPP4 tag
ID, convert the value into DPP4's units/encoding, and WRITE it back as a DPP4
tag. exiftool can write a specific tag *by ID*:

    exiftool -CanonVRD:ID-0x20001:RawBrightnessAdj=2.00 file.CR2
                            ^^^^^^^ DPP4 id, disambiguates from the DPP3 dup

That is exactly what this script automates over a whole folder.

HONEST LIMITATIONS (read these)
-------------------------------
1. This is an APPROXIMATION. DPP4 has a different rendering engine, so even a
   perfect value copy will not look pixel-identical to the DPP3 render. Use it
   to recover your edit *intent*, not a byte-perfect match.
2. Value encodings differ between versions for many settings (ranges, signs,
   fixed-point scaling, structs). You MUST calibrate those on YOUR machine
   (you have DPP3+DPP4; the calibrate command below builds the table for you).
3. Crop and tone-curve became nested STRUCTS in DPP4 (CropInfo 0xf0100,
   ToneCurve 0x20400). The seed map handles the scalar settings; structs are
   marked TODO and are best done after calibration confirms their layout.

ALWAYS run against throw-away COPIES first. Never your originals.

Requires: exiftool (brew install exiftool), Python 3.8+.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
# Tag map: DPP3 tag id  ->  how to write the DPP4 equivalent.
#
# Each entry: dpp3_id (int) : {
#     "dpp4_id":   int,                 # DPP4 CanonVRD tag id
#     "name":      "ExifToolTagName",   # name exiftool expects after the id
#     "xform":     callable|None,       # value transform DPP3 -> DPP4 units
# }
#
# Seeded from the exiftool CanonVRD tag tables. The xform for most entries is
# `identity` as a PLACEHOLDER -- calibration (below) tells you which ones are
# really 1:1 and which need scaling/sign/offset. Treat any xform you have not
# personally calibrated as unverified.
# ----------------------------------------------------------------------------

def identity(v):
    return v

# Brightness is the one pair documented in the wild (DPP3 0x0038 -> DPP4 0x20001)
# and is known to apply 1:1 (a DPP3 value of 2.00 shows as 2.00 in DPP4).
TAGMAP = {
    0x0038: {"dpp4_id": 0x20001, "name": "RawBrightnessAdj",      "xform": identity},   # verified 1:1
    0x0018: {"dpp4_id": 0x20101, "name": "WhiteBalanceAdj",       "xform": identity},   # CALIBRATE: enum remap likely
    0x001a: {"dpp4_id": 0x20102, "name": "WBAdjColorTemp",        "xform": identity},   # CALIBRATE
    0x0002: {"dpp4_id": 0x20301, "name": "PictureStyle",          "xform": identity},   # CALIBRATE: enum codes differ
    0x0115: {"dpp4_id": 0x20303, "name": "ContrastAdj",           "xform": identity},   # CALIBRATE: range/sign
    0x0116: {"dpp4_id": 0x20901, "name": "SaturationAdj",         "xform": identity},   # CALIBRATE
    0x011e: {"dpp4_id": 0x20900, "name": "ColorHue",             "xform": identity},    # CALIBRATE (ColorToneAdj->Hue)
    0x025a: {"dpp4_id": 0x20310, "name": "SharpnessAdj",          "xform": identity},   # CALIBRATE: sharp vs USM
    0x005e: {"dpp4_id": 0x20601, "name": "ChrominanceNoiseReduction", "xform": identity},  # CALIBRATE
    0x005f: {"dpp4_id": 0x20600, "name": "LuminanceNoiseReduction",   "xform": identity},  # CALIBRATE
    # crop + tone curve are STRUCTS in DPP4 -- handled separately below
    # (they can't be expressed as flat id->id pairs).
}

# ----------------------------------------------------------------------------
# Struct settings (DPP4 nests these; DPP3 stored them as flat tags).
#
# exiftool writes a DPP4 struct with serialized syntax, e.g.:
#   -CanonVRD:CropInfo={CropActive=1,CropX=684,CropY=726,CropWidth=3417,CropHeight=2278}
# We assemble that string from the DPP3 flat tags.
# ----------------------------------------------------------------------------

# DPP3 (Ver2) flat crop tag ids -> DPP4 CropInfo struct field names.
# Pixel coordinates are believed 1:1 (both are offsets from the top-left in
# sensor pixels) but CONFIRM with `calibrate` before trusting it on a batch.
CROP_DPP3 = {
    0x0244: "CropActive",   # 0/1
    0x0246: "CropX",        # DPP3 "CropLeft"  -> DPP4 origin X
    0x0248: "CropY",        # DPP3 "CropTop"   -> DPP4 origin Y
    0x024a: "CropWidth",
    0x024c: "CropHeight",
}
DPP4_CROPINFO_NAME = "CropInfo"   # struct tag 0xf0100
# DPP4 crop fields with no DPP3 source -> sane defaults (no rotation).
CROP_DEFAULTS = {"CropRotation": 0, "CropAngle": 0.0}

# DPP3 tone-curve point arrays (int16u[21]) -> DPP4 ToneCurve struct (0x20400).
# The DPP4 struct carries ColorSpace/Shape/InputRange/OutputRange around the
# point list, and the point encoding is NOT confirmed identical -- so this is
# emitted only with --enable-tonecurve and should be calibrated first.
TONE_DPP3 = {
    0x0110: "active",            # ToneCurveActive
    "Luminance": "LuminanceCurvePoints",
    "Red":       "RedCurvePoints",
    "Green":     "GreenCurvePoints",
    "Blue":      "BlueCurvePoints",
}


def build_crop_arg(ids):
    """Return an exiftool -CanonVRD:CropInfo={...} arg, or None if no crop."""
    if 0x0244 not in ids or str(ids[0x0244]).strip() in ("0", "No", "Off"):
        return None
    fields = dict(CROP_DEFAULTS)
    for dpp3_id, field in CROP_DPP3.items():
        if dpp3_id in ids:
            fields[field] = ids[dpp3_id]
    body = ",".join(f"{k}={v}" for k, v in fields.items())
    return f"-CanonVRD:{DPP4_CROPINFO_NAME}={{{body}}}"


def build_tonecurve_arg(ids):
    """
    Return an exiftool ToneCurve struct arg, or None.

    SCAFFOLD ONLY -- the DPP4 point encoding is NOT confirmed. The DPP3 arrays
    are int16u[21] (21 input/output pairs, 0-255). The DPP4 ToneCurve struct
    wraps point data with ColorSpace/Shape/InputRange/OutputRange. Run
    `calibrate` with a known S-curve, read off how DPP4 serialized it, then
    finish this function. Until then `convert` skips tone curves unless you
    pass --enable-tonecurve (which emits this best-effort guess).
    """
    if 0x0110 not in ids or str(ids[0x0110]).strip() in ("0", "No", "Off"):
        return None
    lum = ids.get(0x0118)  # LuminanceCurvePoints, fetched as a string by dump_ids
    if not lum:
        return None
    points = "".join(ch for ch in lum if ch.isdigit() or ch in " ").strip()
    body = f"ColorSpace=0,Shape=1,InputRange=255,OutputRange=255,Points={points}"
    return f"-CanonVRD:ToneCurve={{{body}}}"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def require_exiftool():
    if shutil.which("exiftool") is None:
        sys.exit("exiftool not found. Install it:  brew install exiftool")


def dump_tags(path):
    """Return list of dicts: {id:int, name:str, val:str} for all CanonVRD tags."""
    r = run(["exiftool", "-j", "-a", "-G1", "-H", "-CanonVRD:all", str(path)])
    if r.returncode != 0:
        sys.exit(f"exiftool failed on {path}:\n{r.stderr}")
    data = json.loads(r.stdout)[0]
    out = []
    for key, val in data.items():
        # exiftool -H prefixes with hex id when available; we re-query per-tag
        # below for reliable ids, so here we just keep names/values.
        out.append((key, val))
    return out


def dump_ids(path):
    """Reliable id->value map using exiftool's numeric tag id output."""
    # -D gives decimal tag id, -G1 the group. We parse the human table form.
    r = run(["exiftool", "-D", "-a", "-G1", "-CanonVRD:all", str(path)])
    if r.returncode != 0:
        sys.exit(f"exiftool failed on {path}:\n{r.stderr}")
    ids = {}
    for line in r.stdout.splitlines():
        # format:  <id> [Group] TagName : value
        try:
            head, val = line.split(":", 1)
        except ValueError:
            continue
        parts = head.split()
        if not parts or not parts[0].isdigit():
            continue
        tag_id = int(parts[0])
        ids.setdefault(tag_id, val.strip())
    return ids


def cmd_inspect(args):
    require_exiftool()
    ids = dump_ids(args.file)
    print(f"# CanonVRD tags in {args.file}\n")
    print(f"{'tag id':>10}  {'value'}")
    for tid in sorted(ids):
        present_dpp3 = tid in TAGMAP
        flag = "  <- mappable DPP3" if present_dpp3 else ""
        print(f"0x{tid:05x}  {ids[tid]}{flag}")


def cmd_convert(args):
    require_exiftool()
    src = Path(args.file)
    if not src.exists():
        sys.exit(f"no such file: {src}")

    work = src
    if not args.in_place:
        work = src.with_suffix(src.suffix + ".dpp4copy" + src.suffix)
        shutil.copy2(src, work)
        print(f"[copy] working on {work.name} (original untouched)")

    ids = dump_ids(work)
    writes = []
    skipped = []
    for dpp3_id, spec in TAGMAP.items():
        if dpp3_id not in ids:
            continue
        raw = ids[dpp3_id]
        try:
            val = spec["xform"](raw)
        except Exception as e:  # noqa: BLE001
            skipped.append((dpp3_id, f"xform error: {e}"))
            continue
        arg = f"-CanonVRD:ID-0x{spec['dpp4_id']:x}:{spec['name']}={val}"
        writes.append((dpp3_id, spec, arg, raw, val))

    # struct settings (crop always; tone curve only when explicitly enabled)
    struct_args = []
    crop = build_crop_arg(ids)
    if crop:
        struct_args.append(("crop", crop))
    if args.enable_tonecurve:
        tone = build_tonecurve_arg(ids)
        if tone:
            struct_args.append(("tonecurve(experimental)", tone))

    if not writes and not struct_args:
        print("No DPP3 recipe tags found to migrate (is this file DPP3-edited?).")
        return

    print("\nPlanned DPP3 -> DPP4 writes:")
    for dpp3_id, spec, arg, raw, val in writes:
        note = "" if spec["xform"] is identity else " (xform)"
        print(f"  0x{dpp3_id:04x} -> 0x{spec['dpp4_id']:05x} {spec['name']}: "
              f"{raw} -> {val}{note}")
    for label, arg in struct_args:
        print(f"  [struct] {label}: {arg}")

    if args.dry_run:
        print("\n[dry-run] nothing written. Re-run without --dry-run to apply.")
        return

    cmd = (["exiftool", "-overwrite_original"]
           + [w[2] for w in writes]
           + [a for _, a in struct_args]
           + [str(work)])
    r = run(cmd)
    if r.returncode != 0:
        sys.exit(f"exiftool write failed:\n{r.stderr}")
    print(f"\n[done] wrote {len(writes)} DPP4 tag(s) to {work.name}")
    print("Open it in DPP4 to verify. If a value looks wrong, calibrate that tag.")


def cmd_calibrate(args):
    """
    Build the value-conversion table empirically.

    You provide TWO copies of the SAME image:
      --dpp3 : edited ONLY in DPP3 to known settings
      --dpp4 : the same edit re-created by hand ONLY in DPP4
    This dumps both tag sets side by side so you can see, per setting, how the
    DPP3 value maps to the DPP4 value (1:1, scaled, sign-flipped, enum-remapped).
    Fill those findings back into TAGMAP['xform'].
    """
    require_exiftool()
    a = dump_ids(args.dpp3)
    b = dump_ids(args.dpp4)
    print("DPP3 id   DPP3 value           |  DPP4 id   DPP4 value")
    print("-" * 64)
    # show known mapped pairs first
    for dpp3_id, spec in sorted(TAGMAP.items()):
        av = a.get(dpp3_id, "(absent)")
        bv = b.get(spec["dpp4_id"], "(absent)")
        print(f"0x{dpp3_id:04x}   {av:<20} |  0x{spec['dpp4_id']:05x}  {bv}  [{spec['name']}]")
    print("\n# DPP4-only tags present (candidates for unmapped DPP3 settings):")
    for tid in sorted(b):
        if tid not in {s["dpp4_id"] for s in TAGMAP.values()}:
            print(f"  0x{tid:05x}  {b[tid]}")


def main():
    p = argparse.ArgumentParser(prog="dpp3-dpp4-bridge",
                                description="Migrate Canon DPP3 recipes to DPP4.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect", help="dump CanonVRD tags + flag mappable DPP3 ones")
    pi.add_argument("file")
    pi.set_defaults(func=cmd_inspect)

    pc = sub.add_parser("convert", help="write DPP4 tags from this file's DPP3 recipe")
    pc.add_argument("file")
    pc.add_argument("--dry-run", action="store_true", help="show plan, write nothing")
    pc.add_argument("--in-place", action="store_true", help="modify file directly (default: work on a copy)")
    pc.add_argument("--enable-tonecurve", action="store_true",
                    help="also emit experimental tone-curve struct (calibrate first!)")
    pc.set_defaults(func=cmd_convert)

    pk = sub.add_parser("calibrate", help="diff a DPP3-edited vs DPP4-edited copy of one image")
    pk.add_argument("--dpp3", required=True)
    pk.add_argument("--dpp4", required=True)
    pk.set_defaults(func=cmd_calibrate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
