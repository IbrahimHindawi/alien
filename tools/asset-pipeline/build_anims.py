"""Bake the Tripo-rigged clips listed in animations.json to VAT1 files.

    python tools/asset-pipeline/build_anims.py [--only hero,stalker] [--no-render]

For each asset: the mesh normalisation is recomputed from the cached LOD
triangle lists (exactly as build_assets.py does), blender_bake.py samples
each clip on our LOD0 and LOD1 meshes (skin weights transferred from Tripo's
rigged mesh, root travel and yaw removed, loop seam spread), and this script
quantises the result into shared/assets/anims/<name>.lod<N>.vat
(docs/architecture/vat-format.md), runs verify_vat.py on it, composes a
contact sheet per clip into shared/assets/previews/anims/, and records the
clips in shared/assets/provenance.json under "animations".
"""
import argparse
import datetime
import json
import os
import struct
import subprocess
import sys

import mdmlib
from build_assets import BLENDER, CACHE, HERE, MESHES, PREVIEWS, ASSETS, load_json, save_json

ANIMS = os.path.join(ASSETS, "anims")
CONF = os.path.join(HERE, "animations.json")


def fnv1a(data):
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def write_vat(path, mdm_path, clips):
    mdm = open(mdm_path, "rb").read()
    nv = struct.unpack_from("<I", mdm, 8)[0]
    head = struct.pack("<4I4I", 0x31544156, 1, nv, len(clips), fnv1a(mdm), 0, 0, 0)
    table, blobs = b"", []
    off = 32 + 64 * len(clips)
    for c in clips:
        frames = c["frames"]
        assert all(len(f) == nv for f in frames), "vertex count differs from the MDM"
        lo = [min(p[k] for f in frames for p in f) for k in range(3)]
        hi = [max(p[k] for f in frames for p in f) for k in range(3)]
        cen = [(a + b) / 2 for a, b in zip(lo, hi)]
        ext = [max((b - a) / 2, 1e-6) for a, b in zip(lo, hi)]
        pos = bytearray()
        nrm = bytearray()
        for f in frames:
            for p in f:
                q = [max(-32767, min(32767, round((p[k] - cen[k]) / ext[k] * 32767))) for k in range(3)]
                pos += struct.pack("<4h", q[0], q[1], q[2], 0)
            for t in range(0, nv, 3):
                a, b, cc = f[t], f[t + 1], f[t + 2]
                e1 = [b[k] - a[k] for k in range(3)]
                e2 = [cc[k] - a[k] for k in range(3)]
                n = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
                ln = sum(x * x for x in n) ** 0.5 or 1.0
                # left-handed file: CCW from outside means the cross product points inward
                nb = struct.pack("<4b", *[max(-127, min(127, round(-x / ln * 127))) for x in n], 0)
                nrm += nb * 3
        off = (off + 15) & ~15
        name = c["name"].encode("ascii")[:15].ljust(16, b"\0")
        table += name + struct.pack("<IfII3f3f2I", len(frames), c["fps"], 1, off, *cen, *ext, 0, 0)
        blobs.append((off, bytes(pos) + bytes(nrm)))
        off += len(pos) + len(nrm)
    out = bytearray(head + table)
    for o, blob in blobs:
        out += b"\0" * (o - len(out))
        out += blob
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)
    return len(out)


def contact_sheet(name, clip, raw, height_px=256):
    from PIL import Image, ImageDraw
    files = sorted(f for f in os.listdir(raw) if f.startswith(clip + ".") and f.endswith(".side.png"))
    if not files:
        return None
    rows = []
    for view in ("side", "front"):
        ims = [Image.open(os.path.join(raw, f.replace(".side.", ".%s." % view))).convert("RGB") for f in files]
        rows.append(ims)
    w = rows[0][0].width
    sheet = Image.new("RGB", (w * len(files), w * 2 + 18))
    for r, ims in enumerate(rows):
        for c, im in enumerate(ims):
            sheet.paste(im, (c * w, 18 + r * w))
    ImageDraw.Draw(sheet).text((4, 3), "%s %s: frames %s; rows side, front; fixed camera, cross = origin"
                               % (name, clip, ", ".join(f.split(".")[1] for f in files)), fill=(200, 200, 210))
    os.makedirs(os.path.join(PREVIEWS, "anims"), exist_ok=True)
    p = os.path.join(PREVIEWS, "anims", "%s.%s.png" % (name, clip))
    sheet.save(p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    specs = mdmlib.load_specs()
    sel = load_json(os.path.join(HERE, "selection.json"), {})
    conf = load_json(CONF, {})
    names = [n.strip() for n in a.only.split(",")] if a.only else list(conf)
    prov_path = os.path.join(ASSETS, "provenance.json")
    status = 0
    for name in names:
        c, spec, s = conf[name], specs["assets"][name], sel[name]
        work = os.path.join(CACHE, "work", "%s-%s" % (name, s["task_id"][:8]))
        lods = [mdmlib.load_tris_json(os.path.join(work, "lod%d.json" % d)) for d in range(3)]
        _, xf = mdmlib.normalize_lods(lods, spec)
        bake = os.path.join(CACHE, "bake", name)
        raw = os.path.join(bake, "renders")
        if os.path.isdir(raw):
            for f in os.listdir(raw):
                os.remove(os.path.join(raw, f))
        os.makedirs(bake, exist_ok=True)
        cfg = {"anim_glb": os.path.join(CACHE, c["anim_task"], "model.glb"),
               "source_glb": os.path.join(CACHE, s["task_id"], "model.glb"),
               "yaw_deg": s.get("yaw_deg", -90), "scale": xf["scale"], "offset": list(xf["offset"]),
               "height": spec["height"], "tall": spec["height"] > 1.0 and "hover" not in spec,
               "lods": [{"mdm": os.path.join(MESHES, "%s.lod%d.mdm" % (name, d)),
                         "out": os.path.join(bake, "lod%d.json" % d)} for d in c.get("lods", [0, 1])],
               "clips": c["clips"], "renders": None if a.no_render else raw}
        save_json(os.path.join(bake, "config.json"), cfg)
        r = subprocess.run([BLENDER, "--background", "--factory-startup", "--python",
                            os.path.join(HERE, "blender_bake.py"), "--", os.path.join(bake, "config.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if "BAKE_OK" not in r.stdout:
            sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
            raise RuntimeError("bake failed for " + name)
        align = load_json(cfg["lods"][0]["out"] + ".align.json", {})
        record = {"rig_task": c["rig_task"], "anim_task": c["anim_task"], "rig": c.get("rig"),
                  "presets": c.get("presets"), "alignment": align, "files": {}, "clips": {},
                  "built": datetime.date.today().isoformat(),
                  "licence": "unverified: Tripo output (see licence_research)"}
        for l in cfg["lods"]:
            data = load_json(l["out"], {})
            vat = os.path.join(ANIMS, os.path.basename(l["mdm"]).replace(".mdm", ".vat"))
            size = write_vat(vat, l["mdm"], data["clips"])
            record["files"][os.path.basename(vat)] = size
            for cl in data["clips"]:
                record["clips"].setdefault(cl["name"], {k: (round(v, 5) if isinstance(v, float) else v)
                                                        for k, v in cl.items() if k != "frames"})
                record["clips"][cl["name"]]["frame_count"] = len(cl["frames"])
        v = subprocess.run([sys.executable, os.path.join(HERE, "verify_vat.py"), "--json"] +
                           [os.path.join(ANIMS, f) for f in record["files"]], capture_output=True, text=True)
        rep = json.loads(v.stdout) if v.stdout.strip().startswith("{") else {"error": v.stdout + v.stderr}
        record["validation"] = rep
        status |= v.returncode
        for f, fr in rep.items():
            print("%-22s %s" % (f, "ok" if fr.get("ok") else "FAIL"))
            for cl, m in fr.get("clips", {}).items():
                print("    %-6s frames %3d  net drift %.4f m (%.2f%% H)  sway %.4f m  seam step ratio %.2f  bob %.3f m" % (
                    cl, m["frames"], m["net_drift_m"], 100 * m["net_drift_m"] / spec["height"], m["max_sway_m"],
                    m["seam_step_ratio"], m["bob_m"]))
            for p in fr.get("problems", []):
                print("    - " + p)
        if not a.no_render:
            record["contact_sheets"] = [os.path.relpath(p, ASSETS).replace("\\", "/")
                                        for p in (contact_sheet(name, cl["name"], raw) for cl in c["clips"]) if p]
        prov = load_json(prov_path, {})
        prov.setdefault("animations", {})[name] = record
        save_json(prov_path, prov)
    return status


if __name__ == "__main__":
    sys.exit(main())
