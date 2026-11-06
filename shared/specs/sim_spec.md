# MILLION DEAD: simulation specification (parity contract)

Both engines implement exactly this. The **state hash after N ticks must be
bit-identical** between the C++ and RASM engines for the same scenario: that
is the correctness gate for every benchmark (a benchmark of an engine that
fails parity is invalid).

How exactness is achieved:

- Only IEEE-754 single-precision operations that are correctly rounded:
  `+ - * /`, `sqrt`, `min`/`max` (with the operand order given), compares,
  conversions stated below. **No `rsqrt`/`rcp` approximations, no FMA** in the
  parity category, no `sin`/`cos`/`atan2`/`exp` anywhere in the simulation.
- Every expression is evaluated in the order and grouping written here. C++
  builds with `-ffp-contract=off` and without fast-math, so clang may
  vectorise but never reassociate or contract.
- Iteration orders that affect results (neighbour sums, spawning, grid
  scatter) are fixed below.
- Default MXCSR (round to nearest, no FTZ/DAZ). Both engines must leave it
  alone.

Version: **SIM 3** (milestone 3: swarm with grid separation). Bump the number
on any rule change; the scenario fixtures record the version they were made
with.

## 1. Constants

    TICK_HZ          60
    DT               0.016666668f          (the f32 nearest 1/60: 0x3C888889)
    WORLD_HALF       1024.0f               world is [-1024, 1024) on X and Z
    CELL_SIZE        2.0f
    INV_CELL         0.5f
    GRID_DIM         1024                  cells per axis
    GRID_CELLS       1048576               GRID_DIM * GRID_DIM
    MAX_ENTITIES     1048576
    SEP_RADIUS       1.0f                  R
    SEP_RADIUS2      1.0f                  R * R
    SEP_MAX_NEIGH    16                    contributions per entity per tick, at most
    SEP_STRENGTH     6.0f
    STEER            0.125f                fraction of the velocity error corrected per tick
    SCUTTLER_SPEED   7.0f                  m/s
    HERO_SPEED       9.0f                  m/s
    DIAG             0.70710677f           0x3F3504F3
    BOUND_LO         -1023.5f
    BOUND_HI         1023.5f

## 2. State (structure of arrays)

All arrays have `MAX_ENTITIES` elements, 64-byte aligned, entity `i` at index
`i`. Live entities are `0 .. count-1` (dense; removal is swap-with-last,
milestone 4+).

    pos_x, pos_z, vel_x, vel_z : f32
    hero_x, hero_z             : f32 (one hero)
    tick                       : u32
    count                      : u32

Scratch, rebuilt every tick (contents are part of the work, not the hash):

    cell       : u32[MAX_ENTITIES]     cell index of entity i
    cell_start : u32[GRID_CELLS + 1]   exclusive prefix sums of cell counts
    sorted_x, sorted_z : f32[MAX_ENTITIES]   positions in cell order
    sorted_id  : u32[MAX_ENTITIES]     entity index in cell order

## 3. Random numbers and initial state

PCG32 (`state`, `inc` u64; the standard pcg32 XSH RR output function):

    pcg32_next(rng):
        old = rng.state
        rng.state = old * 6364136223846793005 + rng.inc      (mod 2^64)
        xorshifted = u32(((old >> 18) ^ old) >> 27)
        rot = u32(old >> 59)
        return (xorshifted >> rot) | (xorshifted << ((-rot) & 31))
    pcg32_seed(rng, seed):
        rng.state = 0; rng.inc = (seed << 1) | 1
        pcg32_next(rng); rng.state += seed; pcg32_next(rng)
    unit_f32(u) = f32(u >> 8) * 5.9604645e-08f          (exact: 2^-24)

Scenario init with `seed` and `count`:

    pcg32_seed(rng, seed)
    hero_x = 0.0f; hero_z = 0.0f; tick = 0
    for i in 0 .. count-1:
        a = unit_f32(pcg32_next(rng))
        b = unit_f32(pcg32_next(rng))
        pos_x[i] = (a * 2.0f - 1.0f) * SPAWN_HALF
        pos_z[i] = (b * 2.0f - 1.0f) * SPAWN_HALF
        vel_x[i] = 0.0f; vel_z[i] = 0.0f

with `SPAWN_HALF` given by the scenario (default 1000.0f). Spawning that lands
within 1 m of the hero is allowed.

## 4. One tick

Input for the tick: `keys`, a u8 of bits `W=1 A=2 S=4 D=8` (the recorded
input stream gives one byte per tick).

### 4.1 Hero

    ix = (D ? 1 : 0) - (A ? 1 : 0)        (as f32: -1, 0, 1)
    iz = (W ? 1 : 0) - (S ? 1 : 0)
    if ix != 0 and iz != 0: ix = ix * DIAG; iz = iz * DIAG
    hero_x = hero_x + (ix * HERO_SPEED) * DT
    hero_z = hero_z + (iz * HERO_SPEED) * DT
    hero_x = min(max(hero_x, BOUND_LO), BOUND_HI)      (max first, then min)
    hero_z = min(max(hero_z, BOUND_LO), BOUND_HI)

### 4.2 Grid build (counting sort)

    for i: cx = i32_trunc((pos_x[i] + WORLD_HALF) * INV_CELL)
           cz = i32_trunc((pos_z[i] + WORLD_HALF) * INV_CELL)
           (positions are always inside the bounds, so 0 <= cx, cz < GRID_DIM;
            engines may clamp anyway, which changes nothing)
           cell[i] = cz * GRID_DIM + cx
    counts: cell_start[0 .. GRID_CELLS] = 0
    for i: cell_start[cell[i] + 1] += 1
    prefix: for c in 1 .. GRID_CELLS: cell_start[c] += cell_start[c - 1]
    scatter, i ascending (stable): slot = cursor[cell[i]]++ (cursor starts at cell_start)
           sorted_x[slot] = pos_x[i]; sorted_z[slot] = pos_z[i]; sorted_id[slot] = i

Engines may implement the cursor however they like (a copy of `cell_start`,
or counting down from the end of the cell, provided the resulting order is
the stable ascending one above).

### 4.3 Separation

For each entity `i` (any order: the result for `i` reads only positions from
the start of the tick):

    px = pos_x[i]; pz = pos_z[i]
    cx = cell[i] % GRID_DIM; cz = cell[i] / GRID_DIM
    sx = 0.0f; sz = 0.0f; n = 0
    for oz in -1, 0, 1:                      (outer: rows, ascending z)
      for ox in -1, 0, 1:                    (inner: ascending x)
        if cx+ox or cz+oz is outside 0 .. GRID_DIM-1: skip
        c = (cz+oz) * GRID_DIM + (cx+ox)
        for s in cell_start[c] .. cell_start[c+1]-1:   (ascending)
          if sorted_id[s] == i: continue
          dx = px - sorted_x[s]
          dz = pz - sorted_z[s]
          d2 = dx * dx + dz * dz              ((dx*dx) + (dz*dz))
          if d2 < SEP_RADIUS2:
            w = (SEP_RADIUS2 - d2) * 1.0f     (R2 is 1.0: w = 1.0f - d2)
            sx = sx + dx * w
            sz = sz + dz * w
            n = n + 1
            if n == SEP_MAX_NEIGH: stop all loops for i
    sep_x[i] = sx; sep_z[i] = sz              (per-entity scratch)

Note `d2 < R2` with `d2 == 0` (two entities on the same point) contributes
zero force: that is intended and keeps the rule exact.

### 4.4 Steering and integration

For each entity `i`, after separation is complete for **all** entities:

    dx = hero_x - pos_x[i]
    dz = hero_z - pos_z[i]
    d2 = dx * dx + dz * dz
    if d2 > 1.0e-4f:
        inv = 1.0f / sqrt(d2)
        tx = (dx * inv) * SCUTTLER_SPEED
        tz = (dz * inv) * SCUTTLER_SPEED
    else:
        tx = 0.0f; tz = 0.0f
    tx = tx + sep_x[i] * SEP_STRENGTH
    tz = tz + sep_z[i] * SEP_STRENGTH
    vx = vel_x[i] + (tx - vel_x[i]) * STEER
    vz = vel_z[i] + (tz - vel_z[i]) * STEER
    nx = pos_x[i] + vx * DT
    nz = pos_z[i] + vz * DT
    pos_x[i] = min(max(nx, BOUND_LO), BOUND_HI)
    pos_z[i] = min(max(nz, BOUND_LO), BOUND_HI)
    vel_x[i] = vx; vel_z[i] = vz

`1.0f / sqrt(d2)` is two correctly rounded operations (sqrtps then divps), not
an `rsqrt`. The branch may be implemented as a mask: compute with `d2`
replaced by 1.0f where `d2 <= 1e-4f`, then select zero.

`min`/`max`: x86 `maxps a, b` returns `b` when either is NaN; no NaN can
occur here (all inputs finite), so any correct min/max is equivalent.

### 4.5 End of tick

    tick = tick + 1

## 5. State hash

FNV-1a 64 (offset `0xcbf29ce484222325`, prime `0x100000001b3`) over, in
order: `count` (4 bytes), `tick` (4), `hero_x`, `hero_z` (4 each), then the
raw bytes of `pos_x[0..count)`, `pos_z[0..count)`, `vel_x[0..count)`,
`vel_z[0..count)`. Printed as 16 lowercase hex digits.

## 6. Rendering instance generation (part of the competed CPU work)

Per frame, after the simulation:

- Camera frustum, projected to the ground plane, is given as 4 half-planes
  `(a, b, c)`: a point `(x, z)` is inside when `a*x + b*z + c >= -CULL_MARGIN`
  for all four (`CULL_MARGIN = 1.0f`). The camera module computes the planes;
  the plane computation itself need not be bit-exact between engines, but
  for benchmark parity both engines read the planes from the scenario file.
- LOD by squared distance to the camera's ground focus point `(fx, fz)`:
  `d2 < LOD0_D2` LOD0, `< LOD1_D2` LOD1, otherwise LOD2, with
  `LOD0_D2 = 900.0f` (30 m), `LOD1_D2 = 6400.0f` (80 m).
- Each visible entity writes one 16-byte instance `float4(x, z, vx, vz)` to
  the instance buffer region of its LOD, entities in ascending index order
  within a region. The vertex shader derives heading from `(vx, vz)` and
  the animation phase from the instance id and time, so no transcendental
  runs on the CPU.
- Instance parity: counts per LOD and an FNV-1a hash of each region must
  match between engines.

## 7. What is per entity and what is approximated (reporting)

| system | rate | exact? |
|---|---|---|
| grid build | every tick, every entity | exact |
| separation | every tick, every entity, at most 16 neighbours | exact, capped |
| pursuit + integration | every tick, every entity | exact |
| culling + LOD + instance write | every rendered frame, every entity | exact |

Later milestones add combat, health, death, archetypes and state machines
here, with the same table kept honest.
