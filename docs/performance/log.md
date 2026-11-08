# Performance log

Every significant change: baseline, hypothesis, change, correctness check,
measurement, decision. Numbers come from `python tools/bench.py` (fresh
process per run, 1 warmup + 5 runs, median with min..max, identical state
hash required) and are recorded in `benchmarks/results/history.csv`.

Machine: AMD Ryzen 9 9950X3D (Zen 5, 16 cores, 96 MB L3 on the V-Cache CCD),
NVIDIA RTX 5070 Ti, Windows 11. Single-threaded simulation unless stated.
Cycles are `rdtsc` reference cycles.

## 2026-11-06: baseline (SIM 3, scalar separation)

| entities | us/tick | grid c/e | separate c/e | integrate c/e |
|---|---|---|---|---|
| 100k | 1388 (1376..1564) | 18.9 | 38.9 | 1.95 |
| 1M | 31594 (31151..32254) | 29.1 | 103.7 | 2.64 |

Separation is 80% of a 1M tick.

## 2026-11-06: separation in 8-lane blocks (kept)

- **Hypothesis H1**: the scalar neighbour loop pays a branch mispredict per
  run exit (3 per entity, variable trip counts) and per distance test in
  dense regions.
- **Hypothesis H2**: scattered writes of `sep[i]` in cell order miss cache.
  The probe (write to the sorted slot instead) was **confounded**: wrong
  separation changes the swarm's density and so the work. Discarded; not
  evidence either way.
- **Change**: `separate_v2.rasm`. Neighbour runs are read 8 slots at a time;
  distance test as one `vcmpps`; run length and self excluded by mask; only
  hit lanes are added, in ascending order, so the result is the spec's exact
  sequence of additions. One branch per block instead of per neighbour.
- **Correctness**: self test `separation v2 equals scalar, crowded` (bitwise
  equal `sep` columns on 30 000 entities folded into a 30 m square) and the
  reference hashes all pass, guard pages on and off.

| entities | us/tick | grid c/e | separate c/e | integrate c/e |
|---|---|---|---|---|
| 100k | 1353 (1347..1373) | 19.4 | 36.7 | 1.92 |
| 1M | 17188 (14482..17431) | 33.0 | 37.9 | 2.79 |

H1 confirmed where it applies: 2.7x on separation at 1M, where crowds are
dense. At 100k the swarm is sparse and the gain is small; the remaining cost
there is per-entity row setup. **Kept.**

Next candidates: the grid's fixed per-tick cost (zeroing and prefix-summing
4 MB of counters regardless of population: ~1.5 M cycles, most of the 100k
grid figure), its random count/scatter traffic at 1M, and then threads.

## 2026-11-06: SIM 4 combat, then threads (kept)

SIM 4 adds combat after integration (fire, piercing projectiles, contact,
stable compaction of the dead, ring spawning); the bench keeps the
population constant, so dead aliens respawn on the ring. Single thread,
SIM 4, plain grid:

| entities | us/tick | grid c/e | separate c/e | integrate c/e |
|---|---|---|---|---|
| 100k | 1375 (1371..1387) | 19.2 | 36.2 | 1.92 |
| 1M | 14472 (14468..14651) | 25.3 | 33.0 | 2.62 |

- **Change**: a fixed worker pool (`platform/threads.rasm`; WaitOnAddress
  between jobs, atomic chunk counter, main thread participates). Separation
  (chunks of 2048 sorted slots) and integration (chunks of 16384) are range
  jobs. The counting sort runs as 32 row bands (`grid_mt.rasm`): each band
  streams all cells but counts and scatters only its own, so the sorted order
  is the single-threaded one.
- **Correctness**: every reference scenario is checked at 1 and 16 threads
  (identical hashes; also 2 and 32 by hand), plus a self test comparing the
  banded grid with `sim_grid` on 50 000 crowded entities.
- **Bug found on the way**: workers read the generation counter when they
  started; a worker starting after the first dispatch took that job as
  already seen and the dispatcher waited forever. Workers now start from 0.

16 threads (cycles are wall-clock rdtsc on the main thread, per entity):

| entities | us/tick | grid | separate | integrate |
|---|---|---|---|---|
| 100k | 695 (685..719) | 17.2 | 7.8 | 2.5 |
| 1M | 5130 (5043..5172) | 12.1 | 6.2 | 1.4 |

2.8x on a 1M tick (14.5 -> 5.1 ms); separation scales 5.3x, the grid only
2.1x (every band rereads all 1M cells: 32 x 4 MB per pass) and integration is
bandwidth-bound. At 100k the fixed costs dominate (the grid's 4 MB of
counters). The banded grid is 4.7x slower than `sim_grid` on one thread, so
the single-threaded path keeps `sim_grid`. **Kept.** Next: the grid's
per-band rereads (bin by band once, then sort within bands), and threaded
culling.

## 2026-11-07: the grid binned by band (kept)

- **Hypothesis**: the banded grid's cost at 16 threads is its rereads: each
  of 32 band jobs streamed all 1M entity cells twice (count, scatter), 256 MB
  of traffic per tick, and a dense crowd occupied only ~10 of the 32 bands.
- **Change** (`grid_mt.rasm`): bin once, then sort each band alone. Entity
  chunks compute cells and a histogram of bands; an exclusive prefix (band
  major, chunk minor) places each chunk's run of each band; chunks bucket
  their ids by band (ascending); then one job per band does a counting sort of
  its ids over its own counters (8 rows, 8192 cells: 32 KB, L1). 128 bands of
  8 rows keep dense crowds spread over all threads.
- **Correctness**: the order within every cell is the stable ascending one, so
  the sorted arrays equal `sim_grid`'s; the self test and all reference hashes
  pass at 1 and 16 threads, and the contest's recorded 1M hashes match.

| scenario (16 threads) | before us/tick | after |
|---|---|---|
| 100k sparse | 724 | 541 |
| 1M sparse | 5761 | 3837 |

Grid at 1M, 16 threads: 12.3 -> 5.2 cycles/entity. **Kept.**

## 2026-11-07: separation over 2x2 cells (discarded)

- **Idea**: a cell is 2 m and the radius 1 m, so only the 2x2 cells on the
  entity's side of its cell centre can hold a hit; exact (no hit is skipped,
  order and the 16 cap unchanged; all hashes passed).
- **Measured** A/B, back to back, one thread: 100k sparse separation 36.2 ->
  47.0 cycles/entity (worse even branch-free), 1M dense 292 -> 298 (dense
  crowds reach the 16-neighbour cap early; the extra cells were not the cost).
  **Discarded.**

## 2026-11-07: removing the dead in parallel (kept)

- **Baseline**: combat was 4.48M cycles per tick at 1M on 16 threads, a
  quarter of the tick, almost all of it `sim_compact`: scalar, one branch per
  entity, and every entity after the first death copied one column at a time.
- **Change** (`compact_mt.rasm`): survivors counted per chunk (8 lanes per
  compare, popcnt); prefixes of survivors and deaths; each chunk packs its
  blocks of 8 with `vpermps` through a 256-entry compress table
  (`compact_lut.rasm`, generated) into spare columns at its own offset, the
  last stores before the chunk's end masked so chunks never touch each
  other's slots; the column pointers swap. A tick with no deaths moves
  nothing. Death events come out in ascending entity order, as before.
- **Correctness**: all reference hashes at 1 and 16 threads, guard pages on
  and off.

| 16 threads | combat cycles/tick | us/tick |
|---|---|---|
| 1M before | 4.48M | 4133 |
| 1M after | 2.12M | 3779 |

Single-threaded the full copy costs the same as the old partial scalar copy
(4.46M). **Kept.**

## 2026-11-07: separation with lanes = entities (kept; contest round 2, idea 1)

- **Idea** (from the C rival, docs/performance/contest.md): lanes are up to 8
  consecutive sorted slots of one row; every candidate of the row window is
  broadcast to all lanes. Outside a lane's 3x3 cells a candidate is >= 2 m
  away, so it never hits; the window is read in spec order; lanes that miss
  add +0.0f (exact: the sums never become -0.0f); a per-lane counter keeps
  the 16 cap. No branch on a distance test.
- **Correctness**: new self test `separation v3 (lanes) equals scalar,
  crowded`; every reference hash; the contest's recorded hashes.

| scenario | separate c/e before | after | us/tick before | after |
|---|---:|---:|---:|---:|
| 100k sparse, 1 thread | 36.2 | 25.9 | 1434 | 1139 |
| 1M dense, 1 thread | 292 | 133 | 84999 | 40349 |
| 1M dense, 16 threads | 22.7 | 10.6 | 7186 | 4151 |

**Kept.** The remaining gap at 16 threads is the layout: the rival keeps the
entities in grid order and fuses grid, separation and integration per band.

## 2026-11-07: storage in grid order (kept; contest round 2, idea 2)

- **Idea** (the C rival's): keep the live columns (pos, vel, hp and a new
  creation sequence `seq`) sorted by (cell, seq) instead of by index. The
  spec's index order is creation order (stable removal, appending spawns),
  so (cell, seq) is exactly the spec's neighbour order, and the hash and the
  state dump gather the columns in seq order first (`sim_index_gather`).
- **Change**: the band job of the binned grid now permutes the live columns
  into the spare ones (ids scattered, then creation order repaired within
  cells on the id column, then the six columns gathered slot by slot); the
  sorted views are the live columns themselves and ids are the identity, so
  separation, integration, projectiles and contact read in order with no
  indirection. Compaction moves seq too; spawns take the next seq.
- **Correctness**: self test `grid-ordered storage equals sim_grid,
  crowded`; every reference hash at 1 and 16 threads; the contest's recorded
  1M hashes.

| 16 threads | before us/tick | after | separate c/e before | after |
|---|---:|---:|---:|---:|
| 100k sparse | 491 | 442 | | |
| 1M sparse | 3554 | 3004 | 6.3 | 3.0 |
| 1M dense | 4151 | 4407 | 10.6 | 9.9 |

The grid build is now the cost: at 1M, 16 threads, ~5.8M cycles per tick, of
which the band jobs are 4.4M, and they are memory-bound (about 80 bytes per
entity per tick through L3/DRAM at ~80 GB/s). 64 or 128 bands: within 5%.
The rival's remaining advantages are exactly what removes that traffic: an
incremental rebuild (stayers copied in runs, only movers placed) and fusing
grid, separation and integration per band in cache-resident row buffers.

## 2026-11-07: separation and integration fused (kept; contest round 2, idea 3, part 1)

- **Change** (`separate_v4.rasm`): when a lanes-kernel group has its sums, the
  same 8 slots are integrated at once with sim_integrate's operations in the
  same order; new positions go to the spare columns (other groups still read
  start positions), velocities in place, short groups through a lane mask.
  No sep columns and no second pass over the population.
- **Correctness**: every reference hash, the contest's recorded hashes.

| 16 threads | before us/tick | after |
|---|---:|---:|
| 100k sparse | 442 | 400 |
| 1M sparse | 3004 | 2752 |
| 1M dense | 4407 | 3857 |

**Kept.** Next: the incremental grid rebuild, which removes most of the grid's
memory traffic.

## 2026-11-07: the dead are left to the next grid build (kept)

- **Baseline**: with grid-ordered storage the compaction still moved all six
  columns of every entity whenever anyone died (a few deaths per tick in the
  bench: 24 MB per tick at 1M).
- **Change**: the next tick's grid build copies every survivor anyway, so it
  now skips the dead (no cell, not counted, not bucketed) and sets the new
  population. Combat's deaths pass only counts the living (`sim_alive`, which
  spawning uses) and the dead (kills), and emits the death events. Until the
  build a dead entity stays in storage with hp <= 0, and every reader in
  between ignores it (projectiles, contact, the hash gather, the dump). The
  compress table is gone.
- **Correctness**: every reference hash, the contest's recorded hashes; the
  game runs with kills, gems and shards as before.

| 16 threads | before us/tick | after |
|---|---:|---:|
| 100k sparse | 400 | 357 |
| 1M sparse | 2752 | 2481 |
| 1M dense | 3857 | 3928 (noise; few deaths) |

**Kept.** (The overlay's GPU figure includes the wait for the display when
vsync is on; uncapped it is about 1 ms at 64k visible.)

## 2026-11-07: incremental grid rebuild (kept, small)

- **Change**: stayers (cell unchanged) keep their order, found by binary
  search on the old cells and placed by a sequential scan; only movers (and
  spawns, marked with old cell -1) are bucketed, placed ahead of each cell's
  stayers and slid forward by sequence. The full-population bucket and the
  order repair over every slot are gone.
- **Correctness**: the self test against sim_grid, every reference hash.

| | before us/tick | after |
|---|---:|---:|
| 1M sparse, 1 thread | 15639 | 14880 |
| 1M sparse, 16 threads | 2481 | 2390 |

Small: the band stage stays ~4.6M cycles per tick at 16 threads. Its cost is
moving six columns of every entity through memory, not placing them. What is
left of the gap is the number of passes over memory per tick (bin, build,
separate: about three), against the rival's one (build rows into cache-sized
buffers, separate and integrate them while they are there).

## 2026-11-07: separation and integration fused into the band jobs (kept, neutral)

- **Change**: a band job that has just built its band separates and
  integrates the band's interior rows (lo + 1 .. lo + 6) at once, while they
  are in its cache; a second pass does each band's first and last rows once
  every band is built; integrated positions go to a third position column.
  Only when no band holds more than 1/32 of the population: a dense crowd
  fills few bands, and per-band work then sits on few threads (dense 1M at
  16 threads went 3974 -> 5004 us fused unconditionally), so such ticks
  separate over chunks of slots as before.
- **Correctness**: every reference hash, the contest's recorded hashes; the
  self tests run the build unfused.
- **Measured** (16 threads): 1M sparse 2390 -> 2318..2419, 100k 375 -> 353..365,
  dense unchanged. Within noise. **Kept** for the structure, not the gain.

What remains between this engine and the C rival is the shape of a tick, not
any one kernel: here a tick is six pool phases with a barrier each (bin,
bucket, band build, edges, deaths, area effects), each reading its inputs
from L3/DRAM; the rival builds each band's rows from the previous tick's
neighbouring rows inside one work-stealing pass and never has the global bin
and bucket passes.

## 2026-11-07: the windowed sweep (kept, small)

- **Change**: the fused integration records the tick's largest move on one
  axis (atomic max); the next build bounds row changes by it and, when few
  spawns are pending, each band counts its new members from its own old rows
  widened by that bound plus the spawns, with no global bin or bucket pass
  and one barrier fewer. The band job collects its movers from the same
  window. The first tick and large moves fall back to the full build.
- **Measured** (16 threads): 1M sparse 2390 -> 2301 us, 100k 375 -> 360; the
  window scan costs about what bin and bucket did. **Kept** (one barrier
  fewer per tick, the structure the remaining work builds on).

## 2026-11-07: the fused integration computes the next build's cells (kept)

- **Change**: as the fused integration writes new positions it also writes
  each slot's new cell (a spare cell column) and counts members and movers
  per band (per-call histograms, merged with atomic adds); deaths take their
  entity out of the counts, spawns put theirs in. The next build reads the
  totals directly: no count pass over the population, one barrier fewer.
  Each band job collects its movers from its own old rows widened by the
  tick's largest move, then counts. A self-check flags any disagreement
  between the integration's counts and a band's (zero in every bench run).
- **Correctness**: every reference hash; the contest's recorded hashes.

| 16 threads | before us/tick | after |
|---|---:|---:|
| 100k sparse | 359 | 281..295 |
| 1M sparse | 2231 | 1868..1921 |
| 1M dense | 4451 | 3546..3717 |

**Kept.**

## 2026-11-07: separation fast path (kept) and 16 lanes (discarded)

- **Fast path (kept)**: the candidate loop drops the self test (a lane meets
  itself once: +0.0f and one hit, subtracted), the cap compare and the lane
  mask; per group, if a lane has more than 16 real hits the group is redone
  with the exact capped loop, and a capped group makes the next start exact.
  One thread: dense 1M 38.0 -> 22.4 ms over 50 ticks (33.1 over 300), sparse
  1M 15.3 -> 12.1..13.5 ms; 16 threads 1M 1896 -> 1768, dense 3732 -> 3024.
- **16 lanes (discarded)**: two 8-lane vectors per broadcast candidate, two
  independent add chains, `sx - dx*(d2 - 1)` with one masked factor. Slower
  everywhere (1T dense 33.1 -> 35.3 ms, 100k 1366 -> 1629 us, 16T 1M 1768 ->
  1844): the spills and two tails per group cost more than the shared
  broadcasts saved, and in crowds one capped lane sends all 16 to the exact path.

## 2026-11-07: chunk affinity in the pool (kept, large)

- **Hypothesis**: past 8 threads the tick stopped scaling (1T 13.0 ms, 8T
  2.64, 16T 2.07). Chunks came from one shared counter, so a band landed on a
  random core every tick (often across the CCDs) and its data, written last
  tick by another core, had to travel.
- **Change** (`threads.rasm`): each participant owns a contiguous slice of a
  job's chunks (its own cursor on its own cache line) and steals from the
  other slices in turn when its own is empty. The same band goes to the same
  core every tick, so the data it wrote last tick is in that core's caches.
  Generic: no pinning, no topology, no CPU detection; stealing keeps the load
  even. Results never depend on who ran which chunk.
- **Correctness**: every hash at 1 and 16 threads.

| 16 threads | before us/tick | after |
|---|---:|---:|
| 100k sparse | 283 | 168 |
| 1M sparse | 1752 | 1330 |
| 1M dense | ~3500 | 3520 |

**Kept.**

## 2026-11-07: per-row cap fallback and early exit (kept); quadrant groups (discarded)

- **Kept**: the fast loop snapshots the sums before each window row; a row in
  which a lane passes 16 hits is redone exactly from the snapshot (a lane's
  own row is cz, so past it the restored count drops itself), and the exact
  loop stops once every lane has its 16. Dense 1M: 1T 33.4 -> 31.1 ms, 16T
  3.6 -> 3.0 ms; 100k at 16 threads 168 -> 137 us.
- **Quadrant groups (discarded)**: inside a crowded cell, lanes grouped by
  quadrant (AVX2 gathers) need only a 2x2 window. Exact (every hash), but
  slower at every threshold (dense 1T 31.1 -> 31.4 ms at >= 16 per cell,
  35.5 at >= 10, 39.6 at >= 6): a crowded cell holds ~11, so a quadrant group
  has ~3 lanes of 8, and the gathers cost more than the smaller window saves.
  Profile that led there: dense separation is all fast-path candidates
  (~15M evaluations per tick, ~121 per group of 8), almost none exact.

## 2026-11-07: candidates four per step (discarded)

Four candidates per loop step, the ones past a run's end redirected (cmov) to
a sentinel slot 1e9 m away in the columns' padding. Exact, but neutral in
every cell (e.g. 1M 1T 12205 -> 12364 us, 16T 1345 -> 1339): the row-exit
mispredictions it removes are not where the time is. **Discarded.**

## 2026-11-07: from reading clang's code, and a profile (kept: three; discarded: three)

Reading clang's assembly for the C rival's candidate loop and an AMD uProf
time profile (one thread, 1M: separation 6.0 ms per tick, band build 4.4 ms,
the rest negligible) led to:

- **Kept** — the candidate loop as clang shapes it: `w` masked once (a missed
  lane adds +-0.0f, exact) and four candidates per step reading past the run
  inside the row, bounded by a counter the band owns. A first version bounded
  by the row's end read the next band's unbuilt counter: the large hashes
  failed, the small ones did not, so `tools/test.py` now checks the three
  recorded large hashes at 1 and 16 threads.
- **Kept** — a group's new cells stored with one vector store and counted with
  two popcounts when all lanes share a band (was ~80 instructions per group).
- **Kept** — windowed band counts from the last build's cell sizes minus the
  leavers (vector compare), not an increment chain through memory per stayer.
- **Kept** — pool workers spin 10x longer before sleeping (4-14% at 16 threads).
- **Discarded** — AVX2 gathers for the band's columns (slower than scalar on
  this CPU: 1M 1T 12.1 -> 13.6 ms); a register cursor for stayer placement
  (cell-change branch mispredicts in sparse rows: 10.75 -> 12.46 ms).

| us per tick | 100k 1T | 100k 16T | 1M 1T | 1M 16T | dense 1T | dense 16T |
|---|---:|---:|---:|---:|---:|---:|
| now | 1184 | 134 | 10753 | 1118 | 27989 | 2883 |
| C rival | 1012 | 110 | 9268 | 733 | 22535 | 1920 |

## 2026-11-07: the merge build (kept)

- **Profile** (uProf, 16 threads, line level): in the band job the scalar
  per-slot gather (11.3 s of 30.1) and the stayer placement (6.6 s) were most
  of the build.
- **Change** (windowed builds, as the C rival builds rows): the prefix writes
  the cells' starts directly; the movers are sorted by (cell, seq) (an
  insertion sort when a band has fewer than 256, else counted per cell in
  `sim_mcount` and then insertion by seq within each cell); then the old
  range is streamed in blocks of 8: when a block's last stayer comes before
  the next mover, its stayers are left-packed (a `pdep`/`pext` permutation)
  and the seven columns stored at once; otherwise lane by lane. The output is
  written once, in order: no slot ids, no order repair, no gather.
- **Correctness**: every reference and recorded hash at 1 and 16 threads; a
  guard-page run.

## 2026-11-07: the merge packs stayers in pieces (kept)

The block merge built the 8 stayers' keys (cell << 32 | seq) as two vectors
of 64-bit keys; each step packs every stayer whose key comes before the next
mover's (`vpcmpgtq`; keys are positive, so the signed compare holds; the
no-mover sentinel is the largest signed key: a first version used -1, which
is the smallest signed key, and hung), then emits the movers before the next
stayer. One thread, 1M: 10.66 -> 10.31 ms. At 16 threads the machine was
loaded during the measurement (the C rival itself measured 916-997 us instead
of 733); under that load the new and previous builds were within noise.

## 2026-11-07: steering started early; boundary rows separated in the band job (kept)

- The integration's steering (its sqrt/div chain) is computed at the group's
  start and parked in two stack slots, so it runs alongside the candidate
  loop: one thread 1M 10.31 -> 9.69 ms, 100k 1191 -> 1120 us.
- A band whose lower neighbour is already finished (usually the same
  thread's previous band, with the pool's affinity) separates the two rows
  at their boundary at once and claims it; the edge pass does only unclaimed
  boundaries. Bands publish a per-build stamp after their writes, so a band
  reads only finished neighbours. One thread 100k 1111 -> 1037 us, 1M 9.66 ->
  9.49 ms; 16 threads 1044 -> 1006 us.

| us per tick | 100k 1T | 100k 16T | 1M 1T | 1M 16T | dense 1T | dense 16T |
|---|---:|---:|---:|---:|---:|---:|
| now | 1037 | 123 | 9493 | 1006 | 26433 | 2671 |
| C rival (same session) | 1016 | 110 | 9256 | 741 | 22830 | 1919 |

## 2026-11-07: deaths recorded where they happen (kept)

A hit that takes hp from > 0 to <= 0 appends the slot to a dead list:
projectiles serially, the beam and the bomb (area job, parallel) with an
atomic. The deaths step processes the list (event, no cell, out of the next
build's counts) instead of a parallel scan of every hp and its barrier.
Kills and state are unchanged; only the order of death events (presentation)
can differ. Interleaved A/B on a loaded machine (the rival itself measured
874-977 us at 1M): 1M 16 threads 1324/1387/1366 -> 1196/1227/1294 us, 100k
150/123 -> 131/113 us. **Kept.**

## 2026-11-07: the first win, measured interleaved

With the machine's load drifting, separate runs of the two engines stopped
being comparable, so `tools/duel.py` runs them alternately per cell and takes
medians (5 rounds each). Every hash checked.

| scenario | asm us | C us | C / asm |
|---|---:|---:|---:|
| 100k, 1 thread | 1046 | 1037 | 0.99 |
| 100k, 16 threads | 106 | 112 | **1.06** |
| 1M, 1 thread | 9510 | 9308 | 0.98 |
| 1M, 16 threads | 915 | 734 | 0.80 |
| 1M dense, 1 thread | 26615 | 22956 | 0.86 |
| 1M dense, 16 threads | 2581 | 1942 | 0.75 |

The assembly engine is faster than clang's in one cell, level in two, and
behind in three: the dense crowd and 1M at 16 threads.

## 2026-11-07: exact loop in three parts (kept); sticky exact (discarded)

The exact (capped) loop splits each row's run at the group's own slots: before
and after them no candidate is a lane, so no self test (and `w` masked once).
Dense 1M one thread 26.4 -> 25.0 ms (interleaved A/B). Restoring the sticky
flag (a group after a capped one starts exact) made dense worse (25.0 ->
26.4): redoing only the row that reaches the cap beats redoing whole groups.

Duel, interleaved, loaded machine: 100k 1T 1.03, 100k 16T 1.35 (asm ahead);
1M 1T 0.97, 1M 16T 0.82, dense 1T 0.91, dense 16T 0.79 (C ahead).

## 2026-11-07: cap checkpoints per step (kept); checkpoint-free rows (discarded)

- Checkpoints per 4-candidate step instead of per row (an overflow redoes at
  most one step exactly; the restored counts drop each lane's own hit where it
  was already met): dense 1M one thread 25.5 -> 23.4 ms, 16 threads 3.01 ->
  2.78 ms; sparse pays 1-2%.
- Skipping the checkpoints in rows where no lane can reach 16 (a horizontal
  max per row): slower in sparse (10.05 -> 10.63 ms) and wrong as written
  (padding lanes' counts); discarded.

Duel, interleaved: 100k 1T 0.97, 100k 16T **1.19**, 1M 1T 0.96, 1M 16T 0.76,
dense 1T **1.00**, dense 16T 0.84.

## 2026-11-07: one pass over the window (kept, large); full packing stores (discarded)

- The windowed band build scanned its window twice (a scalar collect of the
  movers, then a vector leaver pass over the old range). One vectorised pass
  now does both: new cell in the band and not the old one -> listed; old cell
  in the band and not the new one -> out of its old cell's count (the old
  sizes are computed first). 1M one thread 9.69 -> 8.87 ms; 16 threads ~990
  -> ~900 us.
- Full stores instead of `vpmaskmovd` in the merge's packing, masked only near
  the band's end: within noise at 16 threads (the packing waits on memory, not
  on the stores). Discarded.

Duel, interleaved, 5 rounds (C / asm, above 1 the assembly is faster):
100k 1T **1.02**, 100k 16T **1.09**, 1M 1T **1.04**, 1M 16T 0.73,
dense 1T **1.04**, dense 16T 0.84.

## 2026-11-08: bands of 4 rows (discarded)

256 bands of 4 rows against 128 of 8, contest matrix, 3 runs, back to back,
every hash OK (us/tick, 8 rows / 4 rows): 100k 1T 1024 / 1092, 100k 16T
107 / 116, 1M 1T 8805 / 9164, 1M 16T 912 / 899, dense 1T 21458 / 21725,
dense 16T 2303 / 2196. Four cells worse, two better (dense 16T by 5%), and
the 100k 16T cell falls behind the C rival (110). **Reverted to 8 rows.**

## 2026-11-08/09: the laptop (Ryzen 9 5900HS, 8 cores / 16 threads, 16 MB L3)

On this machine both engines stop scaling at 4 threads: a copy test reaches
~20 GB/s whatever the thread count, and the large-population cells are bound
by the bytes each entity moves per tick. The assembly engine moved ~112 B per
entity (C ~60), so most of this session removed columns from the per-tick
copy. Every step is exact (all checks at 1 and 16 threads); ratios are
interleaved runs against the previous commit (5 rounds unless noted).

| commit | change | effect |
|---|---|---|
| 9172958 | merge: plain stores for whole blocks and for packed blocks away from the band's end (masked 256-bit stores are microcoded on Zen 3) | 1M 1T -13% (discarded on the 9950X3D, where it was within noise) |
| 1162915 | fused bands integrate in place, into a per-thread buffer, rows copied home when no neighbour row is left to separate | 1M 16T -6% |
| eeca6c4 | no old-cell column: the integration flags a cell change (bit 31), band ranges from the last build's cell starts, leavers' old cells by their neighbours first | 1M 16T -12%, dense 16T -8% |
| dee2bba | no cell column: flags in the sequence (31 changed, 30 dead), cells from the positions the build reads anyway | 1M 16T -8%, 1M 1T -3% |
| f70e581 | hp at the sequence number (the build no longer moves it); area effects gather it; sequences renumbered by rank near the map's end (checked by forcing it every tick) | 1M 16T -12%, dense 16T -9% |
| 227f0f7 | bands of 4 rows again, now with lazily cleared per-band counters (the integration cleared and published all 256 per call) | dense 16T -20% (its bands fuse), others level |
| f1ca2c4 | rows waiting for a boundary job are not stashed: position = clamp(start + velocity * dt) is computed again from the stored velocity, bit for bit | 1M 16T -9%, dense 16T -4% |
| ab87baf | collect: the old-range lane mask only at the range's ends | 1M 1T -1% |
| bec7344 | pool threads at above-normal priority (a preempted worker held up the phase) | 1M 16T -7%, 100k 16T -7% |

Also fixed: the bloom targets were never created (the vcall argument trap:
`r8` passed as the output pointer after vcall had set `r8` to 0); a window
resize then released null pointers and crashed on start on this laptop.

Measured and discarded:
- Fusing the dense crowd with 8-row bands (up to 1/16 of everyone per band,
  bands handed out largest first): level.
- Mover and leaver lists built by the integration instead of the window scan
  (leavers found in the band's and its neighbours' mover lists by old cell):
  the build saved ~3 M cycles per tick at 1M, the integration spent as much
  (~74k movers per tick). Isolated: the per-band flush costs ~2%, packing the
  records ~12% of the integration (~56 cycles per group with a mover, scalar
  or with vector left-packing alike); 16T +3%. Discarded. They were also the
  way to an in-place merge (no read-for-ownership of the output columns,
  ~20 B per entity), which needs the neighbours' old rows no longer read.
- Integer compare for `d2 < 1` (off the FP add pipes): level; the candidate
  loop waits on its in-order sums.
- Dense crowd at 16 threads: the threads are busy 87.5% of the band phase.
  Bands handed out largest first, dealt round-robin to the pool's slices:
  +1.5% (the affinity of bands to cores is lost, and one dense band job is
  ~1/5 of the phase, so the tail stays). Discarded.
- The profiling counters' locked adds (~770 per tick): no measurable cost.
- A higher threshold for the merge's insertion sort, skipping all-stayer
  blocks in the collect, longer worker spin: level or worse.

Contest matrix on this machine, interleaved, 5 rounds, quiet machine
(C / asm, above 1 the assembly is faster):

| scenario | start of session | now |
|---|---:|---:|
| 100k, 1 thread | 1.03 | **1.11** |
| 100k, 16 threads | 1.62 | **1.57** |
| 1M, 1 thread | 0.80 | 0.99 |
| 1M, 16 threads | 0.57 | 0.99 |
| 1M dense, 1 thread | 0.96 | **1.06** |
| 1M dense, 16 threads | 0.60 | 0.98 |

## 2026-11-09: the local band build (kept), the stage counters (fixed)

- **Local band build** (`grid_local.rasm`, 8f91961) for bandwidth-bound
  ticks: windowed and fused builds of large populations on 8 or more
  threads. Each band job rebuilds its rows and one row either side into its
  participant's cache-resident buffer (the region's old cell sizes from the
  last build's starts, a collect over the region widened by the largest
  move, a prefix, the merge), separates and integrates its own rows there
  (sim_separate_v4 now takes all its columns from a per-participant table),
  and streams them out with `vmovntps`: whole lines, never read first. No
  boundary jobs, no waiting rows, no claims. This build's cell starts go to a
  second array, so every job reads the old ones. 1M 16T 3.57 -> 2.72 ms
  (C / asm 0.98 -> 1.31). On one thread it is 16% slower (the two extra rows
  are built twice and memory is not the bottleneck); the crossover is 8
  threads (6T: 3.46 windowed vs 3.59 local; 8T: 3.11 local). The dense crowd
  takes it too but does not gain: its tick is separation compute.
- **Stage counters fixed** (4fc612c): rasm indexes arrays by element, and
  `grid_stage_cyc[8]`, `[16]`, `[24]`, `[32]` were byte offsets: they wrote
  past the 5-element array since the counters were added, lately into the
  local build's row table during the band phase (the hashes passed by
  timing). Found because the counters read garbage in local ticks.
- Dense 16T: largest-first order with the local build, +3 to 5%; the local
  build off for concentrated populations: level. Dense groups are 98% fast
  path (2.4% redo exactly at the cap); ~130 candidates per 8-lane group
  against ~30 in sparse: 16-lane groups sharing each candidate are the known
  remedy (fewer candidate-lane steps per entity).

Contest matrix, interleaved, 5 rounds (C / asm):
100k 1T **1.13**, 100k 16T **1.58**, 1M 1T 0.99, 1M 16T **1.31**,
dense 1T **1.08**, dense 16T 0.98.

## 2026-11-09: help inside band jobs (kept), and the last single-thread tries

- **Help with rows of bands in progress** (8d15021): in the local build a
  band, once built, publishes its columns and rows; its rows are taken one at
  a time by itself and by any participant out of band jobs (a pool_help hook
  in pool_run_chunks); it waits for its helpers before its rows go out. The
  dense crowd's large band jobs no longer leave a tail: dense 16T -3%
  (C / asm 0.98 -> 1.00). Pieces of 1024 slots instead of rows: level.
- In-place integration only with more than one participant (320254e): 1M 1T
  -1%; 2T and 4T keep it (alone the copies cost more than they save).
- Discarded: 8-row bands with the local build (dense 16T +33%), velocities
  copied only for a band's own rows (the prefetcher streams them anyway),
  software prefetch in the merge (level).

Contest matrix, interleaved, 7 rounds (C / asm):
100k 1T **1.12**, 100k 16T **1.55**, 1M 1T 0.99, 1M 16T **1.33**,
dense 1T **1.08**, dense 16T 1.00.
- One thread: integrating into the participant's buffer and streaming each
  call's positions out to sim_pos3 (no read for ownership): +2%. Discarded.

## 2026-11-09: the cap checked once per sparse group (kept)

- Separation (d2ef715): the fast loop saved its state and checked the
  16-neighbour cap at every step of 4 candidates (to redo from that step).
  Sparse groups never reach the cap: they now check it once per group
  (more than 17 hits, each lane meeting itself once) and redo the whole
  group exactly if needed. Dense groups (more than 3 candidates per lane on
  the lanes' own row) keep the per-step check: redoing whole dense groups
  was +17%. 1M 1T -3%, 100k -5%, dense level.
- Discarded: skipping the per-step save and check in dense groups while no
  lane has more than 12 hits (+3 to 8%: the branch mispredicts).

Contest matrix, interleaved, 7 rounds (C / asm):
100k 1T **1.17**, 100k 16T **1.64**, 1M 1T **1.01**, 1M 16T **1.36**,
dense 1T **1.09**, dense 16T 1.00.
