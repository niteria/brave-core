#!/usr/bin/env python3
# Copyright (c) 2026 The Brave Authors. All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://mozilla.org/MPL/2.0/.
"""Cross-build and execute libyuv rows on SME-only and SVE+SME QEMU CPUs.

Requires the Chromium 146 libyuv checkout, Clang/LLD 21+, AArch64 Linux headers,
matching compiler-rt builtins and QEMU user-mode with SME support. Does not build
or install Brave. Uses the real pinned libyuv source and its scalar pixel oracle.
"""
import argparse
import json
from pathlib import Path
import re
import resource
import signal
import subprocess
import tempfile

LIBYUV_REV = '917276084a49be726c90292ff0a6b0a3d571a6af'
ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'patches/third_party/libyuv/BUILD.gn.patch'


def run(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True,
                            timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError(' '.join(args) + '\n' + result.stdout + result.stderr)
    return result.stdout


def march(text, target):
    match = re.search(r'static_library\("' + target + r'"\)(.*?)\n  }', text,
                      re.S)
    if not match:
        raise RuntimeError('Missing libyuv target: ' + target)
    flag = re.search(r'cflags = \[ "(-march=[^"]+)" \]', match[1])
    if not flag:
        raise RuntimeError('Missing architecture flag: ' + target)
    return flag[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--libyuv', type=Path, required=True)
    parser.add_argument('--clang', required=True)
    parser.add_argument('--lld', required=True)
    parser.add_argument('--sysroot', required=True,
                        help='AArch64 Linux sysroot containing include/')
    parser.add_argument('--builtins', required=True,
                        help='matching libclang_rt.builtins-aarch64.a')
    parser.add_argument('--qemu', required=True)
    args = parser.parse_args()
    source = args.libyuv.resolve()
    if run('git', '-C', str(source), 'rev-parse', 'HEAD').strip() != LIBYUV_REV:
        raise RuntimeError('Use the libyuv revision pinned by Brave 1.88.x')
    run('git', '-C', str(source), 'diff', '--quiet', 'HEAD', '--',
        'source', 'include')
    original = run('git', '-C', str(source), 'show', 'HEAD:BUILD.gn')
    # The negative control deliberately gets SIGILL; do not leave core files.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryDirectory(prefix='brave-libyuv-sme-') as temp:
        out = Path(temp)
        (out / 'BUILD.gn').write_text(original)
        run('git', 'apply', '--check', str(PATCH), cwd=out)
        run('git', 'apply', str(PATCH), cwd=out)
        patched = (out / 'BUILD.gn').read_text()
        flags = {'before': march(original, 'libyuv_sme'),
                 'after': march(patched, 'libyuv_sme')}
        common = [args.clang, '--target=aarch64-linux-gnu',
                  '--sysroot=' + args.sysroot, '-std=c++17', '-O2',
                  '-ffreestanding', '-fno-stack-protector',
                  '-ffunction-sections', '-fdata-sections',
                  '-I', str(source / 'include')]

        def compile_file(path, output, flag):
            run(*common, flag, '-c', str(path), '-o', str(output))

        # Both scalar oracle and fixture use the baseline ISA.
        compile_file(source / 'source/row_common.cc', out / 'common.o',
                     '-march=armv8-a')
        compile_file(ROOT / 'script/test/data/libyuv_sme_only.cc',
                     out / 'test.o', '-march=armv8-a')
        report = {'libyuv': LIBYUV_REV, 'flags': flags, 'runs': []}
        for version, flag in flags.items():
            obj = out / (version + '.o')
            compile_file(source / 'source/row_sme.cc', obj, flag)
            binary = out / version
            run(args.lld, '-static', '--gc-sections', '-e', '_start', str(obj),
                str(out / 'common.o'), str(out / 'test.o'), args.builtins,
                '-o', str(binary))
            for sve in (False, True):
                cpu = 'max,sve=' + ('on' if sve else 'off') + ',sme=on'
                result = subprocess.run([args.qemu, '-cpu', cpu, str(binary),
                                         '1' if sve else '0'],
                                        capture_output=True, text=True,
                                        timeout=30)
                expected = -signal.SIGILL if version == 'before' and not sve else 0
                report['runs'].append({'version': version, 'cpu': cpu,
                                       'status': result.returncode,
                                       'expected': expected})
                if result.returncode != expected:
                    raise RuntimeError(json.dumps(report) + '\n' + result.stderr)
        # Ensure every source affected by the GN flag still cross-compiles.
        for name in ('rotate_sme.cc', 'scale_sme.cc'):
            compile_file(source / 'source' / name, out / (name + '.o'),
                         flags['after'])
        compile_file(source / 'source/row_sve.cc', out / 'row_sve.o',
                     march(patched, 'libyuv_sve'))
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
