"""Independent MDM1 validator (stdlib only; shares no code with the writer).

    python tools/asset-pipeline/verify_mdm.py              # every mesh in shared/assets/meshes
    python tools/asset-pipeline/verify_mdm.py scuttler     # all LODs of one asset
    python tools/asset-pipeline/verify_mdm.py path/to/x.lod0.mdm ...
    --json  prints a machine-readable report

Checks: magic, version, reserved, file size, counts, indices in range,
bounds and radius match the vertices, triangle budget per LOD, height within
15% of target (seeker: body height and hover), origin on the ground under the
centre, faces +Z (the emissive feature is ahead of the body at its height), no degenerate triangles,
normals unit length and agreeing with the CCW winding, colours on the
palette (at most 4, glow only in the asset's glow colour with A=255),
anim weights present where the archetype animates, silhouette aspect ratios,
LODs consistent with each other. Exit status 1 when anything fails.
"""
import glob
import json
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
MESH_DIR = os.path.join(ROOT, "shared", "assets", "meshes")
SPEC_PATH = os.path.join(HERE, "assets.json")

HEADER = struct.Struct("<IIII3f3ffI")
VERTEX = struct.Struct("<fffbbbBBBBB")


def read_mdm(path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HEADER.size:
        raise ValueError("file shorter than the 48-byte header")
    h = HEADER.unpack_from(data, 0)
    m = {"magic": h[0], "version": h[1], "vcount": h[2], "icount": h[3],
         "bmin": h[4:7], "bmax": h[7:10], "radius": h[10], "reserved": h[11],
         "size": len(data)}
    expect = HEADER.size + VERTEX.size * m["vcount"] + 4 * m["icount"]
    if len(data) != expect:
        raise ValueError("file is %d bytes, header implies %d" % (len(data), expect))
    verts = []
    off = HEADER.size
    for i in range(m["vcount"]):
        v = VERTEX.unpack_from(data, off)
        verts.append({"p": v[0:3], "n": v[3:6], "w": v[6], "c": v[7:11]})
        off += VERTEX.size
    m["indices"] = list(struct.unpack_from("<%dI" % m["icount"], data, off))
    m["verts"] = verts
    return m


def check(path, specs, problems, info):
    name_m = re.match(r"(.+)\.lod(\d)\.mdm$", os.path.basename(path))
    if not name_m:
        problems.append("file name is not <name>.lod<N>.mdm")
        return None
    name, lod = name_m.group(1), int(name_m.group(2))
    spec = specs["assets"].get(name)
    if spec is None:
        problems.append("no spec for asset '%s' in assets.json" % name)
        return None
    try:
        m = read_mdm(path)
    except Exception as e:  # noqa: BLE001
        problems.append("unreadable: %s" % e)
        return None
    if m["magic"] != 0x314D444D:
        problems.append("bad magic 0x%08X" % m["magic"])
    if m["version"] != 1:
        problems.append("version %d, expected 1" % m["version"])
    if m["reserved"] != 0:
        problems.append("reserved field is %d" % m["reserved"])
    if m["icount"] % 3 or m["icount"] == 0:
        problems.append("index count %d is not a positive multiple of 3" % m["icount"])
    nv = m["vcount"]
    bad = [i for i in m["indices"] if i >= nv]
    if bad:
        problems.append("%d indices out of range" % len(bad))
        return None
    ntri = m["icount"] // 3
    info["triangles"] = ntri
    info["vertices"] = nv
    budget = spec["budgets"][lod] if lod < len(spec["budgets"]) else None
    if budget is None:
        problems.append("LOD %d has no budget" % lod)
    elif ntri > budget:
        problems.append("%d triangles over the LOD%d budget of %d" % (ntri, lod, budget))

    pts = [v["p"] for v in m["verts"]]
    if any(not all(math.isfinite(c) for c in p) for p in pts):
        problems.append("non-finite vertex position")
        return None
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    for i in range(3):
        if abs(lo[i] - m["bmin"][i]) > 1e-4 or abs(hi[i] - m["bmax"][i]) > 1e-4:
            problems.append("header bounds do not match the vertices")
            break
    rad = max(math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) for p in pts)
    if abs(rad - m["radius"]) > 1e-3 * max(1.0, rad):
        problems.append("radius %.4f, vertices give %.4f" % (m["radius"], rad))
    info["bounds_min"] = [round(x, 3) for x in lo]
    info["bounds_max"] = [round(x, 3) for x in hi]

    # height, ground, hover
    target = spec["height"]
    h = hi[1] - lo[1]
    info["height"] = round(h, 3)
    if abs(h - target) > 0.15 * target:
        problems.append("height %.3f outside 15%% of %.3f" % (h, target))
    if "hover" in spec:
        mid = 0.5 * (lo[1] + hi[1])
        if abs(mid - spec["hover"]) > 0.1 * target:
            problems.append("body centre at %.3f, hover is %.3f" % (mid, spec["hover"]))
        if lo[1] < 0.2:
            problems.append("floating unit reaches the ground (min y %.3f)" % lo[1])
    elif abs(lo[1]) > 0.02 * target:
        problems.append("lowest point at y=%.3f, origin should be on the ground" % lo[1])

    # triangles: degeneracy, normals, winding; area-weighted centroid
    degenerate = flipped = badn = 0
    area_sum = 0.0
    cx = cz = 0.0
    glow_c = [0.0, 0.0, 0.0]
    glow_a = 0.0
    colours = {}
    for t in range(ntri):
        ia, ib, ic = m["indices"][3 * t:3 * t + 3]
        a, b, c = pts[ia], pts[ib], pts[ic]
        e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
        area = 0.5 * math.sqrt(cr[0] ** 2 + cr[1] ** 2 + cr[2] ** 2)
        ext = max(math.dist(a, b), math.dist(b, c), math.dist(c, a))
        if area < 1e-9 or area < 1e-6 * ext * ext:
            degenerate += 1
            continue
        for vi in (ia, ib, ic):
            n = [x / 127.0 for x in m["verts"][vi]["n"]]
            ln = math.sqrt(sum(x * x for x in n))
            if abs(ln - 1.0) > 0.03:
                badn += 1
            elif (n[0] * cr[0] + n[1] * cr[1] + n[2] * cr[2]) <= 0:
                flipped += 1
        cen = [(a[i] + b[i] + c[i]) / 3.0 for i in range(3)]
        area_sum += area
        cx += cen[0] * area
        cz += cen[2] * area
        col = m["verts"][ia]["c"]
        colours[col[:3]] = colours.get(col[:3], 0.0) + area
        if col[3] > 0:
            glow_a += area
            for i in range(3):
                glow_c[i] += cen[i] * area
    if degenerate:
        problems.append("%d degenerate triangles" % degenerate)
    if badn:
        problems.append("%d vertex normals not unit length" % badn)
    if flipped:
        problems.append("%d vertex normals disagree with the CCW winding" % flipped)
    if area_sum > 0:
        cx /= area_sum
        cz /= area_sum
        ext = max(hi[0] - lo[0], hi[2] - lo[2])
        info["centroid_xz"] = [round(cx, 3), round(cz, 3)]
        if math.hypot(cx, cz) > 0.15 * ext:
            problems.append("origin is not under the centre (surface centroid at x=%.3f z=%.3f)" % (cx, cz))

    # palette
    pal = {tuple(v): k for k, v in specs["palette"].items()}
    glow_roles = set(specs["glow_roles"])
    used = {}
    for v in m["verts"]:
        rgb = tuple(v["c"][:3])
        role = pal.get(rgb)
        if role is None:
            problems.append("vertex colour #%02X%02X%02X is not on the palette" % rgb)
            break
        used[role] = used.get(role, 0) + 1
        if v["c"][3] > 0 and role not in glow_roles:
            problems.append("emissive alpha on non-glow colour %s" % role)
            break
        if role in glow_roles:
            if role != spec.get("glow"):
                problems.append("glow colour %s, the archetype glows %s" % (role, spec.get("glow")))
                break
            if v["c"][3] != 255:
                problems.append("glow colour %s without full emissive alpha" % role)
                break
    info["colours"] = sorted(used)
    if len(used) > 4:
        problems.append("%d palette colours, at most 4 allowed" % len(used))
    allowed = set(spec["colors"]) | ({spec["glow"]} if spec.get("glow") else set())
    extra = set(used) - allowed
    if extra:
        problems.append("colours outside the asset's set: %s" % ", ".join(sorted(extra)))
    if spec.get("glow") and spec["glow"] not in used:
        problems.append("no emissive %s feature" % spec["glow"])

    # facing +Z: the emissive feature sits in front of the rest of the model
    # at the same heights (eyes ahead of the head, a core ahead of the body)
    if spec["kind"] == "unit" and glow_a > 0:
        gz = glow_c[2] / glow_a
        gys = [pts[i][1] for i in range(nv) if m["verts"][i]["c"][3] > 0]
        y0, y1 = min(gys) - 0.1 * h, max(gys) + 0.1 * h
        band_a = band_z = 0.0
        for t in range(ntri):
            tri = [pts[i] for i in m["indices"][3 * t:3 * t + 3]]
            if m["verts"][m["indices"][3 * t]]["c"][3] > 0:
                continue
            cy = sum(p[1] for p in tri) / 3.0
            if y0 <= cy <= y1:
                e1 = [tri[1][i] - tri[0][i] for i in range(3)]
                e2 = [tri[2][i] - tri[0][i] for i in range(3)]
                ar = 0.5 * math.sqrt((e1[1] * e2[2] - e1[2] * e2[1]) ** 2 + (e1[2] * e2[0] - e1[0] * e2[2]) ** 2
                                     + (e1[0] * e2[1] - e1[1] * e2[0]) ** 2)
                band_a += ar
                band_z += ar * sum(p[2] for p in tri) / 3.0
        bz = band_z / band_a if band_a else 0.0
        info["glow_z"] = round(gz, 3)
        info["glow_band_z"] = round(bz, 3)
        if gz < bz + 0.05 * (hi[2] - lo[2]):
            problems.append("emissive feature at z=%.3f is not ahead of the body (%.3f) at its height: "
                            "model does not face +Z" % (gz, bz))

    # anim weights
    wmax = max(v["w"] for v in m["verts"])
    wcount = sum(1 for v in m["verts"] if v["w"] > 0)
    info["anim_vertices"] = wcount
    mode = spec.get("anim", {}).get("mode", "none")
    if mode == "none" and wmax > 0:
        problems.append("anim weights on an asset that does not animate")
    if mode != "none" and wmax < 128:
        problems.append("anim mode %s but max weight is %d" % (mode, wmax))
    if mode == "legs":
        # the weighted vertices are low: their mean height is in the lower half
        ws = [v for v in m["verts"] if v["w"] > 0]
        if ws and sum(v["p"][1] for v in ws) / len(ws) > 0.5 * hi[1]:
            problems.append("leg weights are not on low vertices")

    # silhouette aspect ratios
    w, l = hi[0] - lo[0], hi[2] - lo[2]
    ratios = {"width_over_height": w / h, "length_over_height": l / h,
              "height_over_width": h / w if w else 0.0}
    info["aspect"] = {k: round(v, 2) for k, v in ratios.items()}
    for key, (rmin, rmax) in spec.get("aspect", {}).items():
        if not rmin <= ratios[key] <= rmax:
            problems.append("silhouette %s %.2f outside [%.2f, %.2f]" % (key, ratios[key], rmin, rmax))
    return name, lod, h, lo, hi


def main(argv):
    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("--")]
    with open(SPEC_PATH, "r", encoding="utf-8") as f:
        specs = json.load(f)
    files = []
    for a in args:
        if a.endswith(".mdm"):
            files.append(a)
        else:
            files += sorted(glob.glob(os.path.join(MESH_DIR, a + ".lod*.mdm")))
    if not args:
        files = sorted(glob.glob(os.path.join(MESH_DIR, "*.mdm")))
    if not files:
        print("verify_mdm: no files to check", file=sys.stderr)
        return 1
    report = {}
    per_asset = {}
    failed = 0
    for path in files:
        problems, info = [], {}
        r = check(path, specs, problems, info)
        if r:
            per_asset.setdefault(r[0], {})[r[1]] = (path, r[2], r[3], r[4])
        report[os.path.basename(path)] = {"ok": not problems, "problems": problems, **info}
    # cross-LOD consistency and completeness
    for name, lods in per_asset.items():
        if sorted(lods) != [0, 1, 2]:
            report.setdefault(name, {"ok": False, "problems": []})
            report[name]["ok"] = False
            report[name]["problems"].append("expected LODs 0,1,2, found %s" % sorted(lods))
        h0 = lods.get(0)
        for lod, (path, h, lo, hi) in lods.items():
            if h0 and abs(h - h0[1]) > 0.1 * h0[1]:
                rep = report[os.path.basename(path)]
                rep["ok"] = False
                rep["problems"].append("height %.3f differs from LOD0 %.3f: LODs must share scale" % (h, h0[1]))
    for k, r in report.items():
        if not r["ok"]:
            failed += 1
    if as_json:
        print(json.dumps(report, indent=1))
    else:
        for k, r in sorted(report.items()):
            status = "ok  " if r["ok"] else "FAIL"
            extra = " %4d tris h=%.3f" % (r["triangles"], r["height"]) if "triangles" in r and "height" in r else ""
            print("%s %s%s" % (status, k, extra))
            for p in r["problems"]:
                print("       - " + p)
        print("%d file(s), %d failed" % (len(report), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
