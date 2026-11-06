"""MILLION DEAD asset pipeline: one entry point.

    python tools/asset-pipeline/build_assets.py                 # build everything
    python tools/asset-pipeline/build_assets.py --only scuttler,hero
    python tools/asset-pipeline/build_assets.py --no-generate   # never call Tripo; use the cache
    python tools/asset-pipeline/build_assets.py --procedural    # placeholders from primitives only
    python tools/asset-pipeline/build_assets.py --no-render

Per asset, `selection.json` says which source is current: "procedural", or
a Tripo task (by id) with the processing settings chosen at review. New Tripo
generations (`--generate N`) add attempts to `attempts.json`; nothing is
selected automatically, since every generated model is reviewed by eye from
the previews first. Downloads are cached in tools/asset-pipeline/cache/
(git-ignored), so a rebuild spends no credits.

Stages: source -> Blender processing (blender_process.py: import, clean,
orient, decimate per LOD, bake texture colour per face, palette snap,
emissive mask) -> normalisation and MDM1 writing (mdmlib.py) -> independent
validation (verify_mdm.py) -> preview renders (blender_render.py) ->
shared/assets/provenance.json.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

import mdmlib
import procedural

HERE = mdmlib.HERE
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
ASSETS = os.path.join(ROOT, "shared", "assets")
MESHES = os.path.join(ASSETS, "meshes")
PREVIEWS = os.path.join(ASSETS, "previews")
CACHE = os.path.join(HERE, "cache")
BLENDER = os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
SELECTION = os.path.join(HERE, "selection.json")
ATTEMPTS = os.path.join(HERE, "attempts.json")
LICENCE = os.path.join(HERE, "licence.json")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def run_blender(script, args):
    cmd = [BLENDER, "--background", "--factory-startup", "--python", os.path.join(HERE, script), "--"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or ("RENDER_OK" not in r.stdout and "PROCESS_OK" not in r.stdout):
        sys.stderr.write(r.stdout[-4000:] + "\n" + r.stderr[-4000:])
        raise RuntimeError("blender %s failed (exit %d)" % (script, r.returncode))
    return r.stdout


# ---------------------------------------------------------------- sources
def build_procedural(name, spec):
    lods = [procedural.BUILDERS[name](d) for d in range(3)]
    steps = ["procedural: convex primitives (procedural.py:%s) at three detail levels" % name,
             "per-part palette roles, emissive part in %s, leg/fin parts masked for animation" % spec.get("glow")]
    return lods, steps


def build_tripo(name, spec, sel):
    import tripo  # only needed for this path
    glb = tripo.cached_model(sel["task_id"])
    if not glb:
        raise RuntimeError("model for task %s is not in the cache; run without --no-generate" % sel["task_id"])
    work = os.path.join(CACHE, "work", name)
    os.makedirs(work, exist_ok=True)
    cfg = {"name": name, "spec": spec, "palette": mdmlib.load_specs()["palette"],
           "yaw_deg": sel.get("yaw_deg", 0), "process": sel.get("process", {})}
    cfg_path = os.path.join(work, "config.json")
    save_json(cfg_path, cfg)
    out = run_blender("blender_process.py", [glb, cfg_path, work])
    report = load_json(os.path.join(work, "report.json"), {})
    lods = [mdmlib.load_tris_json(os.path.join(work, "lod%d.json" % d)) for d in range(3)]
    return lods, report.get("steps", []), report


# ---------------------------------------------------------------- previews
def composite(name, raw_dir):
    from PIL import Image, ImageDraw
    views = [("front", "front"), ("side", "side (+X)"), ("top", "top (front up)"), ("34", "3/4")]
    tiles = []
    for lod in range(3):
        row = []
        for v, label in views:
            p = os.path.join(raw_dir, "%s.lod%d.mdm.%s.png" % (name, lod, v))
            im = Image.open(p).convert("RGB")
            d = ImageDraw.Draw(im)
            d.text((8, 6), "%s lod%d %s" % (name, lod, label), fill=(200, 200, 210))
            row.append(im)
        tiles.append(row)
        sheet = Image.new("RGB", (row[0].width * 2, row[0].height * 2))
        for i, im in enumerate(row):
            sheet.paste(im, ((i % 2) * im.width, (i // 2) * im.height))
        sheet.save(os.path.join(PREVIEWS, "%s.lod%d.png" % (name, lod)))
    w, h = tiles[0][0].width, tiles[0][0].height
    over = Image.new("RGB", (w * 4, h * 3))
    for r, row in enumerate(tiles):
        for c, im in enumerate(row):
            over.paste(im, (c * w, r * h))
    over.save(os.path.join(PREVIEWS, "%s.png" % name))


def render(names):
    raw = os.path.join(CACHE, "renders")
    os.makedirs(PREVIEWS, exist_ok=True)
    files = [os.path.join(MESHES, "%s.lod%d.mdm" % (n, d)) for n in names for d in range(3)]
    run_blender("blender_render.py", [raw, "384"] + files)
    for n in names:
        composite(n, raw)


# ---------------------------------------------------------------- provenance
def write_provenance(results):
    prov = load_json(os.path.join(ASSETS, "provenance.json"), {"assets": {}})
    attempts = load_json(ATTEMPTS, {})
    prov["generated_by"] = "tools/asset-pipeline/build_assets.py"
    prov["format"] = "MDM1, shared/specs/mesh_format.md"
    prov["licence_research"] = load_json(LICENCE, {"status": "not yet researched"})
    for name, r in results.items():
        r["tripo_attempts"] = attempts.get(name, [])
        prov["assets"][name] = r
    save_json(os.path.join(ASSETS, "provenance.json"), prov)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-generate", action="store_true", help="never call the Tripo API")
    ap.add_argument("--generate", type=int, default=0, metavar="N",
                    help="spend up to N new Tripo generations per listed asset (adds attempts, selects nothing)")
    ap.add_argument("--procedural", action="store_true", help="ignore selection.json; build placeholders")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()

    specs = mdmlib.load_specs()
    selection = load_json(SELECTION, {})
    names = [n for n in specs["assets"]]
    if a.only:
        names = [n.strip() for n in a.only.split(",")]
    if a.generate and not a.no_generate:
        import tripo
        for n in names:
            for _ in range(a.generate):
                tripo.generate_attempt(n, specs["assets"][n], ATTEMPTS)

    results = {}
    built = []
    for name in names:
        spec = specs["assets"][name]
        sel = {"source": "procedural"} if a.procedural else selection.get(name)
        if sel and sel["source"] == "procedural" and name not in procedural.BUILDERS:
            sel = None
        if sel is None:
            if name in procedural.BUILDERS:
                sel = {"source": "procedural"}
            else:
                print("%-10s skipped: no source selected" % name)
                continue
        report = {}
        if sel["source"] == "procedural":
            lods, steps = build_procedural(name, spec)
            source = {"type": "procedural", "generator": "tools/asset-pipeline/procedural.py",
                      "note": "placeholder built from primitives; not generated by Tripo"}
        else:
            lods, steps, report = build_tripo(name, spec, sel)
            att = next((x for x in load_json(ATTEMPTS, {}).get(name, []) if x.get("task_id") == sel["task_id"]), {})
            source = {"type": "tripo", "task_id": sel["task_id"], "model_version": att.get("model_version"),
                      "prompt": att.get("prompt"), "negative_prompt": att.get("negative_prompt"),
                      "request": att.get("request"), "generated": att.get("created"),
                      "review": sel.get("review")}
        lods, xf = mdmlib.normalize_lods(lods, spec)
        steps = steps + ["normalised: uniform scale %.4f, offset (%.3f, %.3f, %.3f); origin on the ground under the "
                         "surface centroid%s" % (xf["scale"], *xf["offset"], ", body at hover height" if "hover" in spec else ""),
                         "anim weights: %s" % json.dumps(spec.get("anim")),
                         "written flat-shaded MDM1 by mdmlib.write_mdm (3 vertices per triangle, face normals)"]
        stats = []
        for d, tris in enumerate(lods):
            st = mdmlib.write_mdm(os.path.join(MESHES, "%s.lod%d.mdm" % (name, d)), tris, spec, specs["palette"])
            stats.append(st)
        print("%-10s %-10s tris %s" % (name, sel["source"], [s["triangles"] for s in stats]))
        results[name] = {"source": source, "steps": steps, "lods": stats,
                         "triangles": [s["triangles"] for s in stats],
                         "process_report": {k: v for k, v in report.items() if k != "steps"},
                         "built": datetime.date.today().isoformat()}
        built.append(name)

    v = subprocess.run([sys.executable, os.path.join(HERE, "verify_mdm.py")] + built)
    if not a.no_render and built:
        render(built)
    for n in built:
        results[n]["validated"] = v.returncode == 0
    write_provenance(results)
    return v.returncode


if __name__ == "__main__":
    sys.exit(main())
