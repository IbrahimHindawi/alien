# Asset pipeline

`tools/asset-pipeline` is the only writer of the meshes the two engines load
(`shared/assets/meshes/<name>.lod<N>.mdm`, format in
`shared/specs/mesh_format.md`). It turns text prompts into validated MDM1
files, with previews and a provenance record.

## Rebuild

    python tools/asset-pipeline/build_assets.py --no-generate

rebuilds every asset from `selection.json` and the download cache, spends no
credits, validates, renders previews and rewrites `shared/assets/provenance.json`.
Without a cache (a fresh clone) the Tripo-sourced assets cannot be rebuilt
until their tasks are re-downloaded (`python tools/asset-pipeline/tripo.py fetch <task_id>`,
while Tripo keeps the task) and the committed `.mdm` files stay authoritative.
`--procedural` rebuilds the primitive placeholders, `--only a,b` limits the run.

## Stages

1. **Specs** (`assets.json`): per asset the prompt and negative prompt,
   target height (and hover height), triangle budgets per LOD, the palette
   roles it may use, its glow colour and emissive region, its anim rule, its
   tone split and the silhouette aspect ranges the validator enforces.
2. **Generate** (`tripo.py`, `--generate N` or `tripo.py generate <name>`):
   Tripo v3 text-to-model with the low-poly P-series model `P1-20260311`,
   `face_limit` = the LOD0 budget, texture on, PBR off. Every attempt goes
   into `attempts.json`; downloads into `cache/<task_id>/` (git-ignored).
   Nothing is selected automatically.
3. **Review** (`build_assets.py --candidate name=task_id[:yaw]`): builds the
   task into `cache/candidates/` with previews, leaving `shared/assets`
   alone. A person looks at the previews; an accepted task goes into
   `selection.json` with its yaw and any processing overrides, and the
   reason for the choice.
4. **Process** (`blender_process.py`, Blender 5.1 headless): import the GLB,
   join, weld the vertices glTF split at UV seams, drop loose pieces under
   0.4% of the surface, yaw (P-series models face +X; the default yaw is
   -90 degrees), sample the base-colour texture at 7 points per triangle,
   assign palette roles by luminance rank (the darkest half graphite, the
   lightest tenth bone, the rest gunmetal, per asset in `tone`), mark the
   glow, and build each LOD piece by piece: every connected piece gets a
   share of the budget by area, the main body at least 30%, the smallest
   pieces are dropped (or touching limb pieces clustered) when the budget is
   tight, and a limb piece that will not collapse far enough becomes its
   convex hull. The main body is never hulled; when its collapse loses more
   than 7% of its height (spike tips, heads, feet are the silhouette) it is
   redone with a larger share, up to 70% of the budget, and the limbs give
   way. Each LOD face takes the role the source surface under it voted for
   (glow wins at two votes of seven), so LODs agree; if the glow collapses
   away, the forward-facing faces nearest to it glow instead.
   (Blender 5.1's collapse ignores a vertex group, tried and measured, so
   extremes cannot be pinned that way.)
5. **Normalise and write** (`mdmlib.py`): one transform for all LODs, from
   LOD0: the target height, the surface centroid over the origin, the lowest
   point on the ground (the Seeker: the body centre at hover height).
   Flat shaded, three vertices per triangle, face normals, CCW from outside.
   The pipeline works right-handed (Blender's convention, +X the model's
   left); the file is left-handed as D3D (+X the model's right): `to_file`
   negates X on positions and normals and keeps the index order, which keeps
   triangles CCW seen from outside in left-handed space. Files written
   before 2026-11-06 (commit 8228a15) were mirrored.
6. **Validate** (`verify_mdm.py`, stdlib, shares no code with the writer;
   it reads the triangle budgets from `shared/specs/mesh_format.md` itself,
   checks the winding for left-handed space, and that no body vertex carries
   anim weight).
7. **Preview** (`blender_render.py`): the `.mdm` files themselves are
   rendered (Workbench, flat, vertex colour), front, side, top and 3/4, into
   `shared/assets/previews/<name>.lod<N>.png` and `<name>.png` (all LODs),
   and `<name>.distance.png`: each LOD shrunk until the model is about 50 px
   tall, as a unit is on screen in play, then enlarged 4x pixel for pixel.
8. **Provenance** (`shared/assets/provenance.json`): source, prompt, model
   version, task id, every processing step, triangle counts, all attempts
   with their verdicts, and the licence finding (`licence.json`).

## Emissive mask and anim weights

The glow is the texture's bright, saturated faces inside the archetype's
emissive region (`emissive_region`: in front of the body at the given
heights, within `x_max` of the centre line). P1 textures rarely carry a real
glow, so the usual case is the fallback: the front-most forward-facing faces
of that region, about 1.2% of the surface (the eyes, the visor, the lens).
Those faces get the archetype's glow colour and alpha 255; nothing else has
alpha.

Anim weights come from `anim` in the spec. Animation is in place
(mesh_format.md): the engine moves weighted vertices only, so the body must
carry weight 0. The rules guarantee it (weights only below `leg_top` and
outside `radial_min`, or only on the fins and ring), the validator checks
the files for it, and a weights render (`blender_render.py --weights`)
shows it: bodies black, limbs graded to white. `legs` (Scuttler, Stalker,
Colossus): weight = a height ramp, 1 at the ground to 0 at `leg_top` of the
height, times a radial ramp from the vertical axis (so the belly stays
rigid). `radial` (Seeker): 0 inside `radial_min` of the body radius, 1
beyond `radial_max`, so the ring and fins move. The Hero is 0 for now.
Procedural placeholders also mask the weight to their limb parts.

## Placeholders

`procedural.py` builds each unit from convex primitives (the same
silhouettes, palette, glow and weights) and is the fallback when Tripo is
unavailable. Provenance says which assets are procedural.

## Licence

See `licence.json`: Tripo's terms give commercial rights to paid users only;
the account tier could not be determined from the API, so Tripo-sourced
meshes are marked `unverified`.

## Baked animation (VAT)

`build_anims.py` (with `tripo_anim.py`, `blender_bake.py`, `verify_vat.py`)
rigs and animates through Tripo, bakes the clips onto our LOD0 and LOD1
meshes and writes VAT1 files (`docs/architecture/vat-format.md`) to
`shared/assets/anims/`, with contact sheets in
`shared/assets/previews/anims/`. Tripo endpoints (v3): `POST
/animations/rig-check` (free), `POST /animations/rig` (25 credits), `POST
/animations/retarget` (10 credits per preset). Presets: bipeds get idle,
walk, run and more; quadruped, hexapod and octopod only a walk; nothing
else animates. `animations.json` records which tasks are baked and which
were rejected and why.

    python tools/asset-pipeline/build_anims.py

## Current assets (2026-11-06, budgets v2)

| asset | source | LOD0 / LOD1 / LOD2 triangles |
|---|---|---|
| scuttler | Tripo P1, attempt 2 of 3 | 114 / 60 / 24 |
| stalker | Tripo P1, attempt 2 | 396 / 160 / 60 |
| seeker | Tripo P1, attempt 3 of 3 | 246 / 96 / 40 |
| colossus | Tripo P1, attempt 2 | 1498 / 599 / 199 |
| hero | Tripo P1, attempt 2 | 1467 / 589 / 187 |
| monolith | Tripo P1, attempt 1 | 794 / 293 / 98 |
| pylon | Tripo P1, attempt 1 | 668 / 300 / 100 |
| rock_spire | Tripo P1, attempt 1 | 468 / 272 / 100 |
| hive_spire | Tripo P1, attempt 1 | 757 / 293 / 100 |

All rebuilt from the cached downloads; no regeneration was needed for the
halved budgets. The seeker's LOD2 ring, at 40 triangles, collapses to about
five segments and is 13% shorter than LOD0; that is a reviewed exception
(`lod_height_tolerance` in assets.json).

The reasons for every rejection and acceptance are in `attempts.json` and in
`shared/assets/provenance.json`. Known deviations: the scuttler has eight
legs (the brief says four; three prompts did not get four), the colossus's
red glow is on a front proboscis rather than a chest core, the stalker's
whole head glows rather than separate eyes.
