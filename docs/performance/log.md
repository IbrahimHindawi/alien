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
