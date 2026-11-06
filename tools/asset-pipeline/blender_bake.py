"""Bake Tripo rig animations onto our MDM LODs as VAT1 (run inside Blender).

    blender --background --factory-startup --python blender_bake.py -- config.json

config.json:
  anim_glb      the retargeted GLB (armature + skinned source mesh + actions)
  source_glb    the original text-to-model GLB (to align the rig's rest mesh)
  yaw_deg       the yaw the mesh processing applied
  scale, offset the mesh normalisation (working space, see mdmlib.normalize_lods)
  lods          [{"mdm": path, "out": path.json}] meshes to bake, in MDM vertex order
  clips         [{"name": "walk", "action": "walk", "frames": N}]
  renders       dir for contact-sheet frames (LOD0 only), or null

Writes per LOD a JSON with, per clip: frames of file-space positions and
measurements (root travel and yaw removed, seam residual). Quantising and the
binary file are done by build_anims.py, outside Blender.

Spaces: Blender world (Z up, the rig's frame) <-> working space (right-handed,
Y up, x = Blender x, y = Blender z, z = -Blender y, after the yaw) <-> MDM
file (left-handed: x negated).
"""
import json
import math
import struct
import sys

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1:][0], encoding="utf-8"))
YAW = math.radians(cfg["yaw_deg"])
S, OFF = cfg["scale"], cfg["offset"]


def world_bbox(objs):
    ws = [o.matrix_world @ v.co for o in objs for v in o.data.vertices]
    return Vector([min(w[i] for w in ws) for i in range(3)]), Vector([max(w[i] for w in ws) for i in range(3)])


# ---------------------------------------------------------------- source alignment
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=cfg["source_glb"])
src_lo, src_hi = world_bbox([o for o in bpy.context.scene.objects if o.type == "MESH"])

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=cfg["anim_glb"])
scene = bpy.context.scene
arm = next(o for o in scene.objects if o.type == "ARMATURE")
skinned = [o for o in scene.objects if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)]
skin = max(skinned, key=lambda o: len(o.data.vertices))
for o in scene.objects:
    if o.type == "MESH" and o is not skin:
        o.hide_render = True
        o.hide_viewport = True
arm.data.pose_position = "REST"
bpy.context.view_layer.update()
rig_lo, rig_hi = world_bbox([skin])
ext_s, ext_r = src_hi - src_lo, rig_hi - rig_lo
align_scale = sum(ext_r) / sum(ext_s)
align_off = rig_lo - src_lo * align_scale
report = {"rig_vs_source_scale": align_scale, "rig_vs_source_offset": list(align_off)}

rot = Matrix.Rotation(YAW, 3, "Z")
rot_inv = Matrix.Rotation(-YAW, 3, "Z")


def file_to_world(p):
    w = Vector((-p[0], p[1], p[2]))                     # file -> working
    w = (w - Vector(OFF)) / S                           # un-normalise
    b = Vector((w.x, -w.z, w.y))                        # working -> yawed Blender
    b = rot_inv @ b                                     # un-yaw: source Blender coords
    return b * align_scale + align_off                  # -> rig world


def world_to_file(b):
    b = (b - align_off) / align_scale
    b = rot @ b
    w = Vector((b.x, b.z, -b.y)) * S + Vector(OFF)
    return (-w.x, w.y, w.z)


# ---------------------------------------------------------------- skin weights
deps = bpy.context.evaluated_depsgraph_get()
sm = skin.data
sm.calc_loop_triangles()
mw = skin.matrix_world
s_co = [mw @ v.co for v in sm.vertices]
tris = [tuple(t.vertices) for t in sm.loop_triangles]
bvh = BVHTree.FromPolygons(s_co, tris)
gnames = [g.name for g in skin.vertex_groups]
s_w = [{g.group: g.weight for g in v.groups if g.weight > 0} for v in sm.vertices]


def read_mdm(path):
    data = open(path, "rb").read()
    nv, ni = struct.unpack_from("<II", data, 8)
    verts = [struct.unpack_from("<3f", data, 48 + 20 * i) for i in range(nv)]
    cols = [struct.unpack_from("<4B", data, 48 + 20 * i + 16) for i in range(nv)]
    return verts, cols


def make_lod_object(path):
    verts, cols = read_mdm(path)
    wv = [file_to_world(p) for p in verts]
    me = bpy.data.meshes.new("lod")
    me.from_pydata([tuple(v) for v in wv], [], [(i, i + 1, i + 2) for i in range(0, len(wv), 3)])
    ob = bpy.data.objects.new("lod", me)
    scene.collection.objects.link(ob)
    groups = {n: ob.vertex_groups.new(name=n) for n in gnames}
    for i, p in enumerate(wv):
        loc, nrm, fi, dist = bvh.find_nearest(p)
        a, b, c = tris[fi]
        acc = {}
        # barycentric weights of the nearest point in the source triangle
        bc = barycentric_transform(loc, s_co[a], s_co[b], s_co[c],
                                   Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
        for vi, k in ((a, bc.x), (b, bc.y), (c, bc.z)):
            k = max(0.0, k)
            for g, w in s_w[vi].items():
                acc[g] = acc.get(g, 0.0) + k * w
        tot = sum(acc.values()) or 1.0
        for g, w in acc.items():
            groups[gnames[g]].add([i], w / tot, "REPLACE")
    mod = ob.modifiers.new("arm", "ARMATURE")
    mod.object = arm
    return ob, verts, cols


# ---------------------------------------------------------------- clips
root = next(b for b in arm.pose.bones if b.parent is None)
if arm.animation_data:
    for t in arm.animation_data.nla_tracks:
        t.mute = True
arm.data.pose_position = "POSE"


def root_state():
    m = arm.matrix_world @ root.matrix
    h = m.translation
    y = m.to_3x3() @ Vector((0, 1, 0))
    return Vector((h.x, h.y, 0.0)), math.atan2(y.y, y.x)


def set_action(name):
    act = bpy.data.actions[name]
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = act
    if hasattr(ad, "action_slot") and ad.action_slot is None and len(getattr(act, "slots", [])):
        ad.action_slot = act.slots[0]
    return act


def eval_positions(ob, frame):
    scene.frame_set(int(math.floor(frame)), subframe=frame - math.floor(frame))
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    return [ob.matrix_world @ v.co for v in ev.data.vertices]


lods = [make_lod_object(l["mdm"]) for l in cfg["lods"]]
results = [{"clips": []} for _ in lods]
for clip in cfg["clips"]:
    act = set_action(clip["action"])
    f0, f1 = act.frame_range
    n = clip["frames"]
    fps = scene.render.fps / scene.render.fps_base
    dur = (f1 - f0) / fps
    times = [f0 + (f1 - f0) * i / n for i in range(n + 1)]  # n frames plus the loop end
    roots = []
    for t in times:
        scene.frame_set(int(math.floor(t)), subframe=t - math.floor(t))
        roots.append(root_state())
    h0, a0 = roots[0]
    travel = max((h - h0).length for h, a in roots)
    yaw_dev = max(abs(math.remainder(a - a0, 2 * math.pi)) for h, a in roots)
    for li, (ob, verts0, cols) in enumerate(lods):
        frames = []
        for t, (h, a) in zip(times, roots):
            ps = eval_positions(ob, t)
            # in place: take out the root's horizontal travel and yaw since frame 0
            r = Matrix.Rotation(-(a - a0), 3, "Z")
            ps = [r @ (p - h) + h0 for p in ps]
            frames.append([world_to_file(p) for p in ps])
        # seamless: spread the residual between the loop end and frame 0
        end, start = frames[-1], frames[0]
        resid = [tuple(s[k] - e[k] for k in range(3)) for s, e in zip(start, end)]
        seam_before = max(math.sqrt(sum(x * x for x in r)) for r in resid)
        out = []
        for i, fr in enumerate(frames[:-1]):
            k = i / n
            out.append([[p[0] + d[0] * k, p[1] + d[1] * k, p[2] + d[2] * k] for p, d in zip(fr, resid)])
        results[li]["clips"].append({
            "name": clip["name"], "action": clip["action"], "frames": out, "fps": n / dur, "duration": dur,
            "source_frames": [f0, f1], "root_bone": root.name, "root_travel_removed_m": travel / align_scale * S,
            "root_yaw_removed_deg": math.degrees(yaw_dev), "seam_residual_before_m": seam_before})
        # contact-sheet renders (LOD0)
        if li == 0 and cfg.get("renders"):
            cols0 = cols
            for i in range(0, n, max(1, n // 8)):
                me = bpy.data.meshes.new("fr")
                fr = out[i]
                me.from_pydata([(-p[0], -p[2], p[1]) for p in fr], [], [(j, j + 1, j + 2) for j in range(0, len(fr), 3)])
                attr = me.color_attributes.new("col", "BYTE_COLOR", "CORNER")
                for poly in me.polygons:
                    for lj in poly.loop_indices:
                        r_, g_, b_, a_ = cols0[me.loops[lj].vertex_index]
                        attr.data[lj].color_srgb = (r_ / 255, g_ / 255, b_ / 255, 1.0)
                tmp = bpy.data.objects.new("fr", me)
                results[li].setdefault("render_meshes", []).append((clip["name"], i, tmp.name))
                tmp.hide_render = True
                scene.collection.objects.link(tmp)

for l, r in zip(cfg["lods"], results):
    json.dump({k: v for k, v in r.items() if k != "render_meshes"}, open(l["out"], "w"), separators=(",", ":"))

# ---------------------------------------------------------------- contact sheet frames
if cfg.get("renders"):
    import os
    os.makedirs(cfg["renders"], exist_ok=True)
    for ob, _, _ in lods:
        ob.hide_render = True
    arm.hide_render = True
    skin.hide_render = True
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = 256
    scene.view_settings.view_transform = "Standard"
    sh = scene.display.shading
    sh.light, sh.color_type, sh.background_type = "STUDIO", "VERTEX", "VIEWPORT"
    sh.background_color = (0.035, 0.03, 0.07)
    sh.show_object_outline = True
    # origin marker: a flat grey cross on the ground at the origin, fixed camera
    mk = bpy.data.meshes.new("mk")
    H = cfg["height"]
    q = 0.08 * H
    mk.from_pydata([(-q, -0.005 * H, 0), (q, -0.005 * H, 0), (q, 0.005 * H, 0), (-q, 0.005 * H, 0),
                    (-0.005 * H, -q, 0), (0.005 * H, -q, 0), (0.005 * H, q, 0), (-0.005 * H, q, 0)], [],
                   [(0, 1, 2, 3), (4, 5, 6, 7)])
    mko = bpy.data.objects.new("mk", mk)
    scene.collection.objects.link(mko)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.6 * H if cfg.get("tall") else 2.4 * H
    for view, d in (("side", Vector((1, 0, 0))), ("front", Vector((0, -1, 0)))):
        cam.location = Vector((0, 0, 0.45 * H)) + d * 20 * H
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        cam.data.clip_end = 60 * H
        for clip_name, i, oname in results[0].get("render_meshes", []):
            o = bpy.data.objects[oname]
            o.hide_render = False
            scene.render.filepath = os.path.join(cfg["renders"], "%s.%03d.%s.png" % (clip_name, i, view))
            bpy.ops.render.render(write_still=True)
            o.hide_render = True
json.dump(report, open(cfg["lods"][0]["out"] + ".align.json", "w"))
print("BAKE_OK")
