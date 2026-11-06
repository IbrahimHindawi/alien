# VAT1: baked vertex animation (draft)

Status: draft, written by the asset pipeline (`tools/asset-pipeline/build_anims.py`),
read by `tools/asset-pipeline/verify_vat.py`. No engine reads it yet; the
runtime animation is still the procedural one driven by the MDM anim weight.

One file per mesh per LOD: `shared/assets/anims/<name>.lod<N>.vat`, holding
every clip of that mesh. It belongs to exactly one MDM file: same vertex
count, same vertex order (three vertices per flat-shaded triangle), same
space (left-handed, +X the model's right, +Y up, facing +Z, metres, origin on
the ground under the centre). Little-endian.

## Header (32 bytes)

| offset | type | field |
|---|---|---|
| 0 | `u32` | magic `0x31544156` ("VAT1") |
| 4 | `u32` | version, `1` |
| 8 | `u32` | vertex_count, equal to the MDM file's |
| 12 | `u32` | clip_count |
| 16 | `u32` | mdm_fnv1a: FNV-1a 32 of the whole MDM file it was baked for |
| 20 | `u32[3]` | reserved, 0 |

## Clip table (clip_count x 64 bytes, from offset 32)

| offset | type | field |
|---|---|---|
| 0 | `char[16]` | name, ASCII, zero-padded (`walk`, `run`, `idle`) |
| 16 | `u32` | frame_count |
| 20 | `f32` | fps (frame_count / clip duration) |
| 24 | `u32` | flags: bit 0 loops (frame `frame_count` is frame 0 again) |
| 28 | `u32` | data_offset, from the start of the file, 16-byte aligned |
| 32 | `f32[3]` | pos_center |
| 44 | `f32[3]` | pos_extent (half size per axis) |
| 56 | `u32[2]` | reserved, 0 |

## Clip data (at data_offset)

1. Positions: `frame_count x vertex_count` of `i16[4]` (x, y, z, 0), frame
   major. Position = `pos_center + q / 32767 * pos_extent` per axis.
2. Normals, straight after: `frame_count x vertex_count` of `i8[4]` (x, y, z,
   0), snorm as in MDM (value / 127), the flat face normal of the deformed
   triangle on all three of its vertices.

Each plane maps directly onto a texture of width `vertex_count` and height
`frame_count`: `R16G16B16A16_SNORM` for positions, `R8G8B8A8_SNORM` for
normals; the vertex shader reads row `frame` at column `SV_VertexID` and
lerps two rows. 12 bytes per vertex per frame: the scuttler's LOD0 (342
vertices, one 30-frame walk) is 123 KB; the hero's LOD0 (4401 vertices, run
20 + idle 48 + walk 28 frames) is 5.1 MB, of which the slow idle is half.

## Baked today (2026-11-06)

Tripo auto-rig and preset retarget (`animate_in_place: true`), our LOD0 and
LOD1 meshes skinned by transfer from Tripo's rigged mesh, then made in place
and seamless here:

| mesh | clips (frames) | files |
|---|---|---|
| hero | run (20), idle (48), walk (28) | hero.lod0.vat, hero.lod1.vat |
| stalker | walk (28), run (20) | stalker.lod0.vat, stalker.lod1.vat |
| scuttler | walk (30, Tripo's hexapod walk) | scuttler.lod0.vat, scuttler.lod1.vat |

No clips for the colossus (Tripo's quadruped rig tears its legs; two rigs
tried) or the seeker (not riggable; no hover preset exists). LOD2 keeps the
procedural animation for every unit. Measurements per clip are in
`shared/assets/provenance.json` under `animations`.

## How a clip is made in place and seamless

- Tripo's `animate_in_place` removes the travel, but not the body's offset
  from its rest position (the runs carry the hip about 1 m forward). The
  bake pins the body bone (the hip; the spine for a quadruped; the root
  when every child is a limb) to its rest X/Z position every frame, and the
  root bone to its rest heading. Vertical motion is kept.
- The cycle is found, not assumed: the clip is sampled every half frame and
  the pair of times at least 35% of the range apart with the closest poses
  is the loop (Tripo's runs start from a non-cycle pose: the hero run's
  cycle is frames 9.8 to 24.8 of 0.8 to 30.4). The remaining difference
  between the cycle's end and start is spread linearly over the frames.

## Rules the pipeline enforces (verify_vat.py)

- In place (mesh_format.md, Animation), enforced at two levels:
  - by the bake (build_anims.py fails otherwise): the body bone's X/Z and
    the root's heading are pinned to rest every frame, and the area-weighted
    surface centroid's X/Z drift from the cycle start to the cycle end,
    measured before the seam is closed, is under 1% of the height;
  - by verify_vat.py on the file alone: the loop's mean centroid stays within
    6% of the height of the MDM rest pose's in X/Z (a guard against travel:
    a pose can move mass, an arm or a weapon, without the body moving), and
    the centroid's sway about that mean is under 6%. Vertical bob is allowed
    and reported.
- Seamless loop: the pose after the last frame is frame 0. Any residual is
  spread linearly over the clip before quantising; the step from the last
  frame back to frame 0 must be no larger than 1.5 times the largest step
  between consecutive frames.
- Header, sizes, offsets, vertex count and MDM hash consistent; normals unit
  length; quantisation round-trips within half a step.
