"""Process a generated GLB into per-LOD triangle lists (run inside Blender).

    blender --background --factory-startup --python blender_process.py -- model.glb config.json out_dir

config.json: {"name", "spec", "palette", "yaw_deg", "process": {...}}.
Writes out_dir/lod0.json .. lod2.json (mdmlib triangle lists in MDM space)
and out_dir/report.json (steps and measurements). Normalisation to the
target height and the ground happens afterwards in mdmlib, the same for
every source.

Steps: import and join; delete loose pieces smaller than min_part of the
surface; yaw so the model faces -Y in Blender (+Z in MDM); sample the base
colour texture per source face (several barycentric points, averaged);
decimate (collapse, X symmetry) to each LOD budget; each LOD face takes the
colour of the source surface under it (BVH lookup), so LODs agree; snap to
the asset's palette roles by tone; mark the emissive faces (bright,
saturated texture inside the archetype's emissive region) in the glow colour.
"""
import colorsys
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:]
glb, cfg_path, out_dir = argv[0], argv[1], argv[2]
cfg = json.load(open(cfg_path, "r", encoding="utf-8"))
spec, palette = cfg["spec"], cfg["palette"]
P = {"min_part": 0.004, "glow_sat": 0.45, "glow_val": 0.55, "glow_max_frac": 0.06}
P.update(cfg.get("process", {}))
steps = []
report = {}

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
steps.append("imported GLB with Blender %s glTF importer: %d mesh object(s)" % (bpy.app.version_string, len(meshes)))
for o in bpy.context.scene.objects:
    o.select_set(o.type == "MESH")
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
# bake parent transforms into the mesh
mw = obj.matrix_world.copy()
obj.parent = None
obj.matrix_world = Matrix.Identity(4)
obj.data.transform(mw)

# yaw: rotate about the vertical so the front faces -Y (Blender front view)
yaw = float(cfg.get("yaw_deg", 0))
if yaw:
    obj.data.transform(Matrix.Rotation(math.radians(yaw), 4, "Z"))
    steps.append("rotated %g degrees about the vertical so the model faces +Z in MDM space" % yaw)

me = obj.data
# glTF splits vertices along UV seams and hard edges; weld them back so that
# connected surfaces are one piece again (UVs live on the loops and survive)
wb = bmesh.new()
wb.from_mesh(me)
size = max((max(v.co[k] for v in wb.verts) - min(v.co[k] for v in wb.verts)) for k in range(3))
n_before = len(wb.verts)
bmesh.ops.remove_doubles(wb, verts=wb.verts[:], dist=1e-5 * size)
wb.to_mesh(me)
steps.append("welded %d coincident vertices split by the glTF export (seams)" % (n_before - len(wb.verts)))
wb.free()
me.calc_loop_triangles()
src_tris = len(me.loop_triangles)
report["source_triangles"] = src_tris
steps.append("source mesh: %d triangles, %d vertices" % (src_tris, len(me.vertices)))

# ---------------------------------------------------------------- texture
def find_image(mat):
    if not mat or not mat.use_nodes:
        return None, None
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            inp = n.inputs["Base Color"]
            if inp.is_linked:
                src = inp.links[0].from_node
                # walk through simple colour nodes to the image
                for _ in range(4):
                    if src.type == "TEX_IMAGE":
                        return src.image, None
                    nxt = [i for i in src.inputs if i.is_linked]
                    if not nxt:
                        break
                    src = nxt[0].links[0].from_node
            return None, tuple(inp.default_value)[:3]
    return None, None


images = {}
for i, mat in enumerate(me.materials):
    img, const = find_image(mat)
    if img is not None:
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        images[i] = ("img", px.reshape(h, w, 4), w, h)
    else:
        images[i] = ("const", np.array(const or (0.5, 0.5, 0.5)), 0, 0)
report["materials"] = [(m.name if m else None, images[i][0]) for i, m in enumerate(me.materials)]
uv = me.uv_layers.active.data if me.uv_layers.active else None

SAMPLES = [(1 / 3, 1 / 3, 1 / 3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6),
           (0.45, 0.45, 0.1), (0.1, 0.45, 0.45), (0.45, 0.1, 0.45)]
# image.pixels holds the stored (sRGB-encoded) values of an sRGB texture,
# which is the space the palette is written in.

# ---------------------------------------------------------------- loose parts
bm = bmesh.new()
bm.from_mesh(me)
bm.faces.ensure_lookup_table()
seen = set()
parts = []
for f in bm.faces:
    if f.index in seen:
        continue
    stack, comp = [f], []
    seen.add(f.index)
    while stack:
        g = stack.pop()
        comp.append(g)
        for e in g.edges:
            for h in e.link_faces:
                if h.index not in seen:
                    seen.add(h.index)
                    stack.append(h)
    parts.append(comp)
total_area = sum(f.calc_area() for f in bm.faces)
areas = [sum(f.calc_area() for f in c) for c in parts]
small = [c for c, a in zip(parts, areas) if a < P["min_part"] * total_area]
report["components"] = len(parts)
report["component_area_fractions"] = sorted([round(a / total_area, 4) for a in areas], reverse=True)[:12]
report["large_components"] = sum(1 for a in areas if a >= 0.05 * total_area)
if small:
    bmesh.ops.delete(bm, geom=list({f for c in small for f in c}), context="FACES")
steps.append("removed %d loose pieces under %.1f%% of the surface; %d components, %d large (>=5%%)" %
             (len(small), 100 * P["min_part"], len(parts), report["large_components"]))
bm.to_mesh(me)
bm.free()

# ---------------------------------------------------------------- source BVH
# The source triangles as they were sampled (before deletion) form the colour
# reference; build a BVH over them.
src_bm = bmesh.new()
src_bm.from_mesh(me)
bmesh.ops.triangulate(src_bm, faces=src_bm.faces[:])
src_bm.faces.ensure_lookup_table()


def recolour_lookup():
    """Rebuild per-face colour for the cleaned+triangulated source by sampling
    the texture again on it (its UVs are intact)."""
    uvl = src_bm.loops.layers.uv.active
    cols = np.zeros((len(src_bm.faces), 3))
    for f in src_bm.faces:
        kind, data, w, h = images.get(f.material_index, ("const", np.array((0.5, 0.5, 0.5)), 0, 0))
        if kind == "const" or uvl is None:
            cols[f.index] = data[:3]
            continue
        uvs = [l[uvl].uv for l in f.loops]
        acc = np.zeros(3)
        for b in SAMPLES:
            u = b[0] * uvs[0][0] + b[1] * uvs[1][0] + b[2] * uvs[2][0]
            v = b[0] * uvs[0][1] + b[1] * uvs[1][1] + b[2] * uvs[2][1]
            acc += data[int((v % 1.0) * (h - 1) + 0.5), int((u % 1.0) * (w - 1) + 0.5), :3]
        cols[f.index] = acc / len(SAMPLES)
    return cols


src_rgb = recolour_lookup()
steps.append("sampled the base colour texture at %d points per source triangle" % len(SAMPLES))
src_area = np.array([f.calc_area() for f in src_bm.faces])
src_bvh = BVHTree.FromBMesh(src_bm)

# ---------------------------------------------------------------- frame
# MDM space is (x, z, -y) of Blender space.
def to_mdm(v):
    return (v.x, v.z, -v.y)


co = [to_mdm(v.co) for v in src_bm.verts]
lo = [min(c[i] for c in co) for i in range(3)]
hi = [max(c[i] for c in co) for i in range(3)]
H = hi[1] - lo[1]
cen = [(lo[i] + hi[i]) / 2 for i in range(3)]
report["source_bounds_mdm"] = [lo, hi]

# ---------------------------------------------------------------- palette roles
def lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


hsv = np.array([colorsys.rgb_to_hsv(*np.clip(c, 0, 1)) for c in src_rgb])
L = np.array([lum(c) for c in src_rgb])

# palette distance of the raw texture (sRGB 0..255), area weighted
pal_rgb = {k: np.array(v, dtype=float) for k, v in palette.items()}
allowed = list(spec["colors"]) + ([spec["glow"]] if spec.get("glow") else [])
d_raw = np.array([min(np.linalg.norm(c * 255 - pal_rgb[r]) for r in allowed) for c in src_rgb])
report["raw_palette_distance_mean"] = float((d_raw * src_area).sum() / src_area.sum())
report["raw_within_40"] = float(src_area[d_raw <= 40].sum() / src_area.sum())
report["raw_luminance_quartiles"] = [float(x) for x in np.percentile(L, [10, 25, 50, 75, 90])]
report["raw_mean_saturation"] = float((hsv[:, 1] * src_area).sum() / src_area.sum())

# glow: bright, saturated texture in the archetype's emissive region
glow = np.zeros(len(src_rgb), dtype=bool)
reg = spec.get("emissive_region")
cent = np.array([to_mdm(f.calc_center_median()) for f in src_bm.faces])
nrm = np.array([to_mdm(f.normal) for f in src_bm.faces])
if spec.get("glow") and reg:
    half_len = (hi[2] - lo[2]) / 2
    half_w = (hi[0] - lo[0]) / 2
    band = ((cent[:, 1] - lo[1]) >= reg["y_min"] * H) & ((cent[:, 1] - lo[1]) <= reg["y_max"] * H)
    # "in front" is measured against the body at those heights (the head for
    # eyes), so a long weapon or tail does not move the reference
    z_ref = float(np.median(cent[band, 2])) if band.any() else cen[2]
    in_reg = band & ((cent[:, 2] - z_ref) >= reg["z_min"] * half_len) & \
        (np.abs(cent[:, 0] - cen[0]) <= reg.get("x_max", 1.0) * half_w)
    score = hsv[:, 1] * hsv[:, 2]
    cand = in_reg & (hsv[:, 1] >= P["glow_sat"]) & (hsv[:, 2] >= P["glow_val"])
    cap = P["glow_max_frac"] * src_area.sum()
    acc = 0.0
    for i in np.argsort(-score):
        if cand[i] and acc + src_area[i] <= cap:
            glow[i] = True
            acc += src_area[i]
    report["glow_candidates_in_region"] = int(cand.sum())
    if not glow.any() and P.get("glow_fallback", True):
        # no clear glow in the texture: the front-most forward-facing faces of
        # the region (the eyes, the visor, the lens) glow instead
        idx = np.where(in_reg & (nrm[:, 2] > 0.35))[0]
        if len(idx):
            order = idx[np.argsort(-(cent[idx, 2] + 0.02 * H * L[idx]))]
            acc = 0.0
            for i in order:
                if acc > P.get("glow_fallback_frac", 0.012) * src_area.sum():
                    break
                glow[i] = True
                acc += src_area[i]
            report["glow_fallback"] = ("no saturated glow in the region; the front-most forward-facing %.1f%% "
                                       "of the surface used" % (100 * acc / src_area.sum()))
    report["glow_area_fraction"] = float(src_area[glow].sum() / src_area.sum())

# tone: rank the other faces by luminance (area weighted). The darkest
# tone.dark of the surface takes the first colour (graphite on aliens), the
# lightest tone.bone the last (bone), the rest the middle one. Rank, not
# absolute value, because the generator's textures are uniformly dull: the
# relative light and dark is what carries the plates and ridges.
tone = {"dark": 0.5, "bone": 0.1}
tone.update(spec.get("tone", {}))
tone.update(P.get("tone", {}))
dark, mid, light = (spec["colors"] + [spec["colors"][-1]] * 3)[:3]
ng = ~glow
roles = np.empty(len(src_rgb), dtype=object)
if ng.any():
    sub = np.where(ng)[0]
    order = np.argsort(L[sub])
    cum = np.cumsum(src_area[sub][order]) / src_area[sub].sum()
    rank = np.empty(len(sub))
    rank[order] = cum
    for j, i in enumerate(sub):
        roles[i] = dark if rank[j] <= tone["dark"] else (light if rank[j] > 1.0 - tone["bone"] else mid)
roles[glow] = spec.get("glow")
report["role_area_fractions"] = {r: float(src_area[roles == r].sum() / src_area.sum()) for r in allowed}
steps.append("palette: faces ranked by texture luminance, darkest %d%% -> %s, lightest %d%% -> %s, rest -> %s; "
             "glow %s on bright saturated texture in region %s (%.1f%% of the surface)%s" %
             (100 * tone["dark"], dark, 100 * tone["bone"], light, mid, spec.get("glow"), reg,
              100 * report.get("glow_area_fraction", 0.0),
              ("; " + report["glow_fallback"]) if "glow_fallback" in report else ""))
glow_centre = cent[glow].mean(axis=0) if glow.any() else None

# ---------------------------------------------------------------- LODs
def _decimate_obj(o, target):
    """Collapse-decimate object o in place to at most target triangles."""
    base = o.data.copy()
    base.calc_loop_triangles()
    n0 = len(base.loop_triangles)
    if n0 <= target:
        return n0
    ratio, n = target / n0, n0
    bpy.context.view_layer.objects.active = o
    for _ in range(15):
        o.data = base.copy()
        mod = o.modifiers.new("dec", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
        o.data.calc_loop_triangles()
        n = len(o.data.loop_triangles)
        if n <= target:
            break
        ratio *= 0.9
    return n


def _components(bmx):
    bmx.faces.ensure_lookup_table()
    seen, comps = set(), []
    for f in bmx.faces:
        if f.index in seen:
            continue
        stack, comp = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            comp.append(g.index)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        comps.append(comp)
    return comps


def _piece_bmesh(bmx, face_ids):
    pb = bmesh.new()
    vmap = {}
    for i in face_ids:
        f = bmx.faces[i]
        vs = []
        for v in f.verts:
            if v.index not in vmap:
                vmap[v.index] = pb.verts.new(v.co)
            vs.append(vmap[v.index])
        try:
            pb.faces.new(vs)
        except ValueError:
            pass
    return pb


def _reduce_piece(pb, share, allow_hull=True):
    """Bring one piece to at most share triangles: collapse decimation, or its
    convex hull decimated when the collapse cannot get there (never for the
    main body, which keeps whatever the collapse reached)."""
    pm = bpy.data.meshes.new("piece")
    pb.to_mesh(pm)
    po = bpy.data.objects.new("piece", pm)
    bpy.context.scene.collection.objects.link(po)
    n = _decimate_obj(po, share)
    how = "collapse"
    if n > share and allow_hull:
        hb2 = pb.copy()
        res = bmesh.ops.convex_hull(hb2, input=hb2.verts[:])
        junk = [g for g in res["geom_interior"] + res["geom_unused"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.delete(hb2, geom=junk, context="VERTS")
        bmesh.ops.triangulate(hb2, faces=hb2.faces[:])
        hb2.to_mesh(po.data)
        hb2.free()
        n = _decimate_obj(po, share)
        how = "hull"
    out = bmesh.new()
    out.from_mesh(po.data)
    bpy.data.objects.remove(po)
    return out, how


def _cluster(bmx, info, a_total):
    """Merge pieces that touch (bounding boxes within 2% of the model size)
    into clusters, so a limb made of several segments becomes one piece. The
    core pieces (the largest, and any with core_frac of the surface) stay alone,
    or every limb would merge through the body."""
    core_frac = P.get("core_frac", 0.1)
    boxes = []
    for a, c in info:
        cs = [v.co for i in c for v in bmx.faces[i].verts]
        boxes.append((Vector([min(p[k] for p in cs) for k in range(3)]), Vector([max(p[k] for p in cs) for k in range(3)])))
    size = max((max(b[1][k] for b in boxes) - min(b[0][k] for b in boxes)) for k in range(3))
    eps = 0.02 * size
    parent = list(range(len(info)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    core = [i == 0 or a >= core_frac * a_total for i, (a, _) in enumerate(info)]
    for i in range(len(info)):
        for j in range(i + 1, len(info)):
            if core[i] or core[j]:
                continue
            (l1, h1), (l2, h2) = boxes[i], boxes[j]
            if all(l1[k] - eps <= h2[k] and l2[k] - eps <= h1[k] for k in range(3)):
                parent[find(i)] = find(j)
    groups = {}
    for i, (a, c) in enumerate(info):
        g = groups.setdefault(find(i), [0.0, []])
        g[0] += a
        g[1] += c
    return sorted(((a, c) for a, c in groups.values()), key=lambda x: -x[0])


def make_lod(budget):
    """Decimate to the budget piece by piece: every connected piece gets a
    share of the budget by surface area^0.75 (at least min_piece_tris), the
    smallest pieces are dropped while the minimum shares do not fit, and a
    piece that will not collapse to its share becomes its convex hull."""
    bmx = bmesh.new()
    bmx.from_mesh(obj.data)
    bmesh.ops.triangulate(bmx, faces=bmx.faces[:])
    bmx.faces.ensure_lookup_table()
    bmx.verts.ensure_lookup_table()
    if len(bmx.faces) <= budget:
        geo, note = bmx, "no reduction needed"
    else:
        comps = _components(bmx)
        info = sorted(((sum(bmx.faces[i].calc_area() for i in c), c) for c in comps), key=lambda x: -x[0])
        minimum = P.get("min_piece_tris", 4)
        a_total = sum(a for a, _ in info)
        keep = budget // minimum
        clustered = False
        if len(info) > keep and sum(a for a, _ in info[keep:]) > 0.03 * a_total:
            info = _cluster(bmx, info, a_total)
            clustered = True
        n_all = len(info)
        core_min = int(P.get("core_share", 0.3) * budget)
        while len(info) > 1 and (len(info) - 1) * minimum + max(minimum, core_min) > budget:
            info.pop()
        wts = [a ** 0.75 for a, _ in info]
        shares = [max(minimum, int(budget * w / sum(wts))) for w in wts]
        # the main body keeps at least core_share of the budget, so it stays a volume
        shares[0] = max(shares[0], core_min)
        # never more than a piece has
        shares = [min(s, len(c)) for s, (_, c) in zip(shares, info)]
        while sum(shares) > budget:
            # trim the largest share that is above the minimum, the body last
            ks = [i for i in range(len(shares)) if shares[i] > (core_min if i == 0 else minimum)]
            k = max(ks, key=lambda i: shares[i]) if ks else 0
            shares[k] -= 1
        hulls = 0
        geo = bmesh.new()
        for k, (area, c) in enumerate(info):
            if k >= len(shares):
                break
            share = shares[k]
            pb = _piece_bmesh(bmx, c)
            bmesh.ops.triangulate(pb, faces=pb.faces[:])
            if len(pb.faces) > share:
                if k == 0:
                    # the body: if the collapse eats its extremes (the height
                    # is the silhouette), give it more of the budget
                    def ext(b):
                        zs = [v.co.z for v in b.verts]
                        return max(zs) - min(zs)
                    h0 = ext(pb)
                    sh = share
                    while True:
                        red, how = _reduce_piece(pb, sh, allow_hull=False)
                        if ext(red) >= 0.93 * h0 or sh >= int(0.7 * budget):
                            break
                        red.free()
                        sh = min(int(0.7 * budget), int(sh * 1.25) + 1)
                    share = shares[0] = sh
                else:
                    red, how = _reduce_piece(pb, share)
                pb.free()
                pb = red
                hulls += how == "hull"
            if k == 0 and len(pb.faces) > share:
                # the body would not collapse that far: the limbs give way,
                # the smallest pieces first, then the largest shares
                over = len(pb.faces) - share
                shares[0] = len(pb.faces)
                while over > 0 and len(shares) > 1:
                    ks = [i for i in range(1, len(shares)) if shares[i] > minimum]
                    if ks:
                        j = max(ks, key=lambda i: shares[i])
                        shares[j] -= 1
                        over -= 1
                    else:
                        over -= shares.pop()
            pb.verts.ensure_lookup_table()
            vm = [geo.verts.new(v.co) for v in pb.verts]
            for f in pb.faces:
                try:
                    geo.faces.new([vm[v.index] for v in f.verts])
                except ValueError:
                    pass
            pb.free()
        bmesh.ops.triangulate(geo, faces=geo.faces[:])
        geo.faces.ensure_lookup_table()
        if len(geo.faces) > budget:  # rounding guard: drop the smallest faces
            for f in sorted(geo.faces, key=lambda f: f.calc_area())[:len(geo.faces) - budget]:
                geo.faces.remove(f)
        note = "%d of %d %s kept, %d as convex hulls" % (len(info), n_all,
                                                         "touching-piece clusters" if clustered else "pieces", hulls)
    tris = []
    gname = spec.get("glow")
    for f in geo.faces:
        if f.calc_area() < 1e-10:
            continue
        vs = [l.vert.co.copy() for l in f.loops]
        votes = {}
        for b in SAMPLES:
            p = vs[0] * b[0] + vs[1] * b[1] + vs[2] * b[2]
            hit = src_bvh.find_nearest(p)
            if hit[2] is not None:
                votes[roles[hit[2]]] = votes.get(roles[hit[2]], 0) + 1
        if gname and votes.get(gname, 0) >= 2:  # small features win at two votes of seven
            role = gname
        else:
            votes.pop(gname, None)
            role = max(votes, key=votes.get) if votes else dark
        tris.append([list(to_mdm(v)) for v in vs] + [role, role == gname, True])
    if gname and glow_centre is not None and not any(t[3] == gname for t in tris):
        # the feature collapsed away: the forward-facing faces nearest to where
        # it was glow instead, so every LOD keeps its one luminous accent
        def fdist(t):
            c = [sum(t[k][i] for k in range(3)) / 3 for i in range(3)]
            e1 = [t[1][i] - t[0][i] for i in range(3)]
            e2 = [t[2][i] - t[0][i] for i in range(3)]
            nz = e1[0] * e2[1] - e1[1] * e2[0]
            return sum((c[i] - glow_centre[i]) ** 2 for i in range(3)) + (0.0 if nz > 0 else 1e3)
        for t in sorted(tris, key=fdist)[:max(1, len(tris) // 60)]:
            t[3], t[4] = gname, True
        note += "; glow re-placed on the faces nearest the source feature"
    geo.free()
    return tris, note


report["lods"] = []
for d, budget in enumerate(spec["budgets"]):
    tris, note = make_lod(budget)
    with open(os.path.join(out_dir, "lod%d.json" % d), "w", encoding="utf-8") as f:
        json.dump(tris, f)
    report["lods"].append({"budget": budget, "triangles": len(tris), "pieces": note})
    steps.append("LOD%d: %d triangles (budget %d), per-piece collapse decimation (%s); per-face colour by majority "
                 "vote of the source roles under 7 points" % (d, len(tris), budget, note))

report["steps"] = steps
with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, indent=1)
print("PROCESS_OK")
