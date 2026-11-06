"""Map a crash rva printed by md_rasm.exe to its .rasm source line.

    python tools/symbolize.py 0x1a2b3 [more rvas]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'build' / 'md_rasm.exe'
BASE = 0x140000000  # lld's default image base for x64 executables

for arg in sys.argv[1:]:
    addr = BASE + int(arg, 16)
    out = subprocess.run(['llvm-symbolizer', f'--obj={EXE}', hex(addr)], capture_output=True, text=True)
    print(f'{arg}: ' + ' | '.join(l for l in out.stdout.splitlines() if l.strip()))
