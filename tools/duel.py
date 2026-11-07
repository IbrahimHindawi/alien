"""Interleaved comparison of two engines on the contest's scenarios.

    python tools/duel.py [--rounds 5] [--a build/md_rasm.exe] [--b C:/devel/alien-rival/build/rival.exe]

For each scenario and thread count the two executables run alternately
(a, b, a, b, ...), so machine load drifts hit both alike; medians are
reported with the ratio b / a (above 1: a is faster). Every run's hash must
equal the recorded one.
"""
import argparse
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCENARIOS = [(100000, 600, 1000, 'ab8666237ff52a1c'), (1000000, 300, 1000, '7a1a32542db8e5ab'),
             (1000000, 300, 300, '9f1e580c29fd4f94')]


def run(exe, n, ticks, half, threads):
    out = subprocess.run([exe, '--bench-sim', '--entities', str(n), '--ticks', str(ticks), '--seed', '1',
                          '--spawn-half', str(half), '--threads', str(threads)],
                         capture_output=True, text=True, timeout=1800).stdout
    return int(re.search(r'us_per_tick=(\d+)', out).group(1)), re.search(r'hash=([0-9a-f]{16})', out).group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rounds', type=int, default=5)
    ap.add_argument('--a', default=str(ROOT / 'build' / 'md_rasm.exe'))
    ap.add_argument('--b', default='C:/devel/alien-rival/build/rival.exe')
    a = ap.parse_args()
    bad = 0
    for n, ticks, half, want in SCENARIOS:
        for threads in (1, 16):
            ta, tb = [], []
            for _ in range(a.rounds):
                for exe, ts in ((a.a, ta), (a.b, tb)):
                    us, h = run(exe, n, ticks, half, threads)
                    bad += h != want
                    ts.append(us)
            ma, mb = statistics.median(ta), statistics.median(tb)
            print(f'n={n:>8} half={half:>5} threads={threads:>2}: a {ma:>8} us  b {mb:>8} us  b/a {mb / ma:5.2f}', flush=True)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
