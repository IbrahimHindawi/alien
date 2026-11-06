"""Build MILLION DEAD: the shared shaders and the RASM engine.

    python tools/build.py                 # everything
    python tools/build.py rasm            # pick targets: shaders, rasm

Outputs go to build/: md_rasm.exe, shaders/*.cso, assets/ (copied). Guard
pages are a run-time option of the same executable (--diag).
The MSVC/Windows SDK library environment is discovered with vswhere, as in
x64x11's build; clang/lld come from LLVM_HOME (default C:\\Program Files\\LLVM).
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
RIN = Path(os.environ.get('RIN', r'C:\devel\rin-windows-x64\rin.exe'))

SHADERS = [  # (file, entry, profile, output)
    ('swarm.hlsl', 'vs_unit', 'vs_5_0', 'unit_vs.cso'),
    ('swarm.hlsl', 'ps_unit', 'ps_5_0', 'unit_ps.cso'),
    ('swarm.hlsl', 'vs_ground', 'vs_5_0', 'ground_vs.cso'),
    ('swarm.hlsl', 'ps_ground', 'ps_5_0', 'ground_ps.cso'),
    ('swarm.hlsl', 'vs_hero', 'vs_5_0', 'hero_vs.cso'),
    ('swarm.hlsl', 'vs_particle', 'vs_5_0', 'particle_vs.cso'),
    ('swarm.hlsl', 'vs_ui', 'vs_5_0', 'ui_vs.cso'),
    ('swarm.hlsl', 'ps_ui', 'ps_5_0', 'ui_ps.cso'),
]


def environment():
    env = os.environ.copy()
    if not env.get('LIB'):
        pf86 = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))
        vswhere = pf86 / 'Microsoft Visual Studio/Installer/vswhere.exe'
        install = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires',
                                           'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                           '-property', 'installationPath'], text=True).strip()
        vc = sorted((Path(install) / 'VC/Tools/MSVC').iterdir())[-1]
        kits = pf86 / 'Windows Kits/10'
        sdk = sorted(p for p in (kits / 'Lib').iterdir() if (p / 'um/x64/kernel32.lib').exists())[-1].name
        env['LIB'] = ';'.join(str(p) for p in [vc / 'lib/x64', kits / 'Lib' / sdk / 'um/x64', kits / 'Lib' / sdk / 'ucrt/x64'])
        env['INCLUDE'] = ';'.join(str(p) for p in [vc / 'include', kits / 'Include' / sdk / 'ucrt',
                                                   kits / 'Include' / sdk / 'shared', kits / 'Include' / sdk / 'um',
                                                   kits / 'Include' / sdk / 'winrt'])
        env['WINDOWSSDKVERSION'] = sdk
        env['FXC'] = str(kits / 'bin' / sdk / 'x64' / 'fxc.exe')
    llvm = Path(os.environ.get('LLVM_HOME', r'C:\Program Files\LLVM')) / 'bin'
    env['PATH'] = str(llvm) + os.pathsep + env.get('PATH', env.get('Path', ''))
    env.pop('Path', None)
    return env


def run(cmd, env, cwd=ROOT):
    print('  ' + ' '.join(str(c) for c in cmd), flush=True)
    if subprocess.run([str(c) for c in cmd], cwd=cwd, env=env).returncode != 0:
        sys.exit('build failed')


def build_shaders(env, config):
    out = BUILD / 'shaders'
    out.mkdir(parents=True, exist_ok=True)
    fxc = env.get('FXC') or 'fxc'
    for src, entry, profile, dst in SHADERS:
        flags = ['/nologo', '/O3', '/T', profile, '/E', entry, '/Fo', out / dst]
        if config == 'diag':
            flags[1:2] = ['/Od', '/Zi']
        run([fxc, *flags, ROOT / 'shared/shaders' / src], env)


def copy_assets():
    for sub in ('shared/assets/meshes', 'shared/fixtures'):
        src = ROOT / sub
        if src.is_dir():
            dst = BUILD / 'assets' / Path(sub).name
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.iterdir():
                if f.is_file():
                    shutil.copy2(f, dst / f.name)


def build_rasm(env, config):
    exe = BUILD / 'md_rasm.exe'
    if not RIN.is_file():
        sys.exit(f'no rin at {RIN}; set RIN')
    run([RIN, 'asm', 'main.rasm', '-o', exe, '-l', 'd3d11', '-l', 'dxgi', '-l', 'dxguid', '-l', 'gdi32', '-l', 'winmm', '-l', 'synchronization'],
        env, cwd=ROOT / 'engine-rasm')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('targets', nargs='*', default=['shaders', 'rasm'])
    ap.add_argument('--config', choices=['release', 'diag'], default='release')
    args = ap.parse_args()
    env = environment()
    BUILD.mkdir(exist_ok=True)
    for t in args.targets:
        t0 = time.perf_counter()
        print(f'[{t}]', flush=True)
        if t == 'shaders':
            build_shaders(env, args.config)
            copy_assets()
        elif t == 'rasm':
            build_rasm(env, args.config)
        else:
            sys.exit(f'unknown target {t}')
        print(f'[{t}] {time.perf_counter() - t0:.2f}s', flush=True)


if __name__ == '__main__':
    main()
