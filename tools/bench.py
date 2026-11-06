"""Reproducible simulation benchmark with history.

    python tools/bench.py                       # default scenario set, 5 runs each
    python tools/bench.py --runs 7 --entities 1000000 --ticks 600
    python tools/bench.py --note "separation: SIMD neighbour blocks"

Each run is a fresh process (`md_rasm.exe --bench-sim`): same seed, same
recorded input (the bench key schedule), so runs differ only by machine
noise. One warmup run is discarded. Reports the median and the min..max
spread of microseconds per tick and of cycles per entity per phase, checks
that every run produced the same state hash (a benchmark whose hash differs
from the reference is invalid: run tools/test.py first), and appends one row
per scenario to benchmarks/results/history.csv with the commit and the note.
"""
import argparse
import csv
import datetime
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'build' / 'md_rasm.exe'
HISTORY = ROOT / 'benchmarks' / 'results' / 'history.csv'
SCENARIOS = [(100000, 600), (1000000, 300)]


def run_once(n, ticks, seed):
    out = subprocess.run([str(EXE), '--bench-sim', '--entities', str(n), '--ticks', str(ticks), '--seed', str(seed)],
                         capture_output=True, text=True, timeout=900).stdout
    us = int(re.search(r'us_per_tick=(\d+)', out).group(1))
    g, s, i = (int(x) / 100 for x in re.search(r'grid=(\d+) separate=(\d+) integrate=(\d+)', out).groups())
    h = re.search(r'hash=([0-9a-f]+)', out).group(1)
    return us, g, s, i, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--entities', type=int)
    ap.add_argument('--ticks', type=int, default=300)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--note', default='')
    a = ap.parse_args()
    scenarios = [(a.entities, a.ticks)] if a.entities else SCENARIOS
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain', 'engine-rasm'], capture_output=True, text=True).stdout.strip()
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    new = not HISTORY.exists()
    with HISTORY.open('a', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['date', 'commit', 'dirty', 'entities', 'ticks', 'runs', 'us_tick_median', 'us_tick_min', 'us_tick_max',
                        'grid_cpe', 'separate_cpe', 'integrate_cpe', 'hash', 'note'])
        for n, ticks in scenarios:
            run_once(n, ticks, a.seed)  # warmup
            rs = [run_once(n, ticks, a.seed) for _ in range(a.runs)]
            hashes = {r[4] for r in rs}
            if len(hashes) != 1:
                sys.exit(f'nondeterministic: {hashes}')
            us = [r[0] for r in rs]
            med = lambda k: statistics.median(r[k] for r in rs)
            print(f'n={n:>8} ticks={ticks}: {statistics.median(us):>7} us/tick (min {min(us)}, max {max(us)})  '
                  f'cycles/entity grid {med(1):.2f} separate {med(2):.2f} integrate {med(3):.2f}  hash {rs[0][4]}')
            w.writerow([datetime.datetime.now().isoformat(timespec='seconds'), commit, 'yes' if dirty else 'no', n, ticks,
                        a.runs, statistics.median(us), min(us), max(us), med(1), med(2), med(3), rs[0][4], a.note])


if __name__ == '__main__':
    main()
