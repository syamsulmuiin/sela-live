#!/usr/bin/env python3
"""Build an offline, relocated source payload; never install or import runtime code."""
import ast
import hashlib
import json
import re
from pathlib import Path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(value):
    text = str(value)
    path = Path(value)
    if not path.is_absolute() or any(p in ('.', '..') for p in text.split('/')) or '\\' in text:
        raise ValueError('Absolute non-traversing path required')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink path or ancestor refused: ' + text)
    return path


def read_source(path):
    safe_path(path)
    try:
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError('Not a bounded regular source file: ' + str(path))
        return path.read_bytes()
    except OSError as exc:
        raise ValueError('Missing or unreadable source: ' + str(path)) from exc


def relative_source(name):
    if not isinstance(name, str) or not name or '\\' in name or any(p in ('', '.', '..') for p in name.split('/')) or Path(name).is_absolute():
        raise ValueError('Unsafe relative source path')
    return name


def verify_group(root, files, reader=read_source):
    if not isinstance(files, dict) or not files:
        raise ValueError('Empty source inventory')
    for name, expected in files.items():
        relative_source(name)
        if not name.endswith('.py') or digest(reader(root / name)) != expected:
            raise ValueError('Source hash mismatch: ' + name)


def literal_binding(worker, name, expected):
    try:
        tree = ast.parse(worker)
    except (SyntaxError, ValueError) as exc:
        raise ValueError('Invalid worker Python') from exc
    stores = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == name]
    pattern = rb'(?m)^' + name.encode() + rb'''[ \t]*=[ \t]*(["'])([a-f0-9]{64})\1[ \t]*(?:\#[^\r\n]*)?\r?$'''
    matches = list(re.finditer(pattern, worker))
    if len(stores) != 1 or len(matches) != 1 or matches[0].group(2).decode() != expected:
        raise ValueError('Worker binding mismatch or non-unique literal: ' + name)
    return matches[0].span(2)


NATURAL_FILES = frozenset(('natural.py', 'utterance.py', 'duration.py', 'natural_worker.py', 'duration_worker.py'))
RUNTIME_MANIFEST = 'sela_live_tts/natural-runtime-manifest.json'


def build_runtime(source_app_root, target_app_root, output_root, *, write=False, expected_worker_sha256=None,
                  expected_natural_worker_sha256=None):
    """Return an exact plan, optionally materializing it only under a NEW output.

    All roots must be absolute, symlink-free and pairwise disjoint. The output
    parent must already exist. Source provenance is a local review trust anchor,
    not an online signature check. Bind a reviewed worker with the optional SHA.
    The package report excludes itself from ``files`` to avoid a hash cycle.
    """
    source, target, output = map(safe_path, (source_app_root, target_app_root, output_root))
    def overlaps(a, b):
        return a == b or a in b.parents or b in a.parents
    if overlaps(source, target) or overlaps(output, source) or overlaps(output, target):
        raise ValueError('Source, target and staging output must be disjoint')
    if not source.is_dir() or output.exists() or not output.parent.is_dir() or (target.exists() and not target.is_dir()):
        raise ValueError('Source must exist, output must be new with existing parent, target must be a directory')
    for directory in ('sela_live_tts', 'vendor/supertonic'):
        root = safe_path(source / directory)
        for path in root.rglob('*'):
            safe_path(path)
    snapshot = {}
    def read(path):
        if path not in snapshot:
            snapshot[path] = read_source(path)
        return snapshot[path]
    original_manifest = read(source / 'sela_live_tts/upstream-manifest.json')
    manifest = json.loads(original_manifest)
    for group, directory in (('guard', 'voice_app'), ('live', 'sela_live_tts')):
        if manifest[group + '_root'] != str(source / directory):
            raise ValueError('Native source root does not match staging source')
        verify_group(source / directory, manifest[group + '_python_files'], read)
    if set(manifest['guard_python_files']) != {'approved_conditioning.py', 'automatic_conditioning.py', 'supertonic3_worker.py'}:
        raise ValueError('Preserved guard inventory mismatch')
    if not {'__init__.py', 'source_pins.py', 'controls.py'} <= set(manifest['live_python_files']) or 'worker.py' in manifest['live_python_files']:
        raise ValueError('Native live pins incomplete or cyclic')
    provenance = json.loads(read(source / 'vendor/SUPERTONIC-PROVENANCE.json'))
    sdk = source / 'vendor/supertonic'
    verify_group(sdk, provenance['python_files'], read)
    actual = {str(p.relative_to(sdk)) for p in sdk.rglob('*.py')}
    if actual != set(provenance['python_files']):
        raise ValueError('Vendor Python inventory mismatch')
    if (provenance['python_files'] != manifest['sdk_python_files'] or
            provenance['commit'] != manifest['sdk_commit'] or
            provenance['repository'] != manifest['sdk_repository']):
        raise ValueError('Vendor provenance differs from native manifest')
    if provenance['license'] != 'supertonic-LICENSE.txt' or digest(read(source / 'vendor/supertonic-LICENSE.txt')) != provenance['license_sha256']:
        raise ValueError('Vendor license mismatch')
    payload = {}
    for directory in ('voice_app', 'sela_live_tts', 'vendor/supertonic'):
        paths = ((source / directory / name) for name in manifest['guard_python_files']) if directory == 'voice_app' else (source / directory).rglob('*.py')
        for path in paths:
            payload[str(path.relative_to(source))] = read(path)
    present_natural = {name for name in NATURAL_FILES if 'sela_live_tts/' + name in payload}
    if present_natural and present_natural != NATURAL_FILES:
        raise ValueError('Incomplete natural runtime bundle')
    if expected_natural_worker_sha256 is not None and not present_natural:
        raise ValueError('Reviewed natural worker is absent')
    for relative in ('vendor/supertonic-LICENSE.txt', 'vendor/SUPERTONIC-PROVENANCE.json'):
        payload[relative] = read(source / relative)
    for group, directory in (('guard', 'voice_app'), ('live', 'sela_live_tts'), ('sdk', 'vendor/supertonic')):
        manifest[group + '_root'] = str(target / directory)
    manifest_raw = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    source_files = {p: digest(b) for p, b in sorted(payload.items())}
    source_files['sela_live_tts/upstream-manifest.json'] = digest(original_manifest)
    worker = payload['sela_live_tts/worker.py']
    if expected_worker_sha256 is not None and digest(worker) != expected_worker_sha256:
        raise ValueError('Worker bytes differ from explicitly reviewed digest')
    start, end = literal_binding(worker, 'MANIFEST_SHA', digest(original_manifest))
    literal_binding(worker, 'PIN_HELPER_SHA', digest(payload['sela_live_tts/source_pins.py']))
    transformed = worker[:start] + digest(manifest_raw).encode() + worker[end:]
    payload['sela_live_tts/worker.py'] = transformed
    payload['sela_live_tts/upstream-manifest.json'] = manifest_raw
    natural = payload.get('sela_live_tts/natural_worker.py')
    if natural is not None:
        start, end = literal_binding(natural, 'MANIFEST_SHA', digest(original_manifest))
        literal_binding(natural, 'PIN_HELPER_SHA', digest(payload['sela_live_tts/source_pins.py']))
        if expected_natural_worker_sha256 is not None and digest(natural) != expected_natural_worker_sha256:
            raise ValueError('Natural worker bytes differ from explicitly reviewed digest')
        payload['sela_live_tts/natural_worker.py'] = natural[:start] + digest(manifest_raw).encode() + natural[end:]
        runtime_files = {p: digest(b) for p, b in sorted(payload.items())
                         if p.endswith('.py') or p == 'sela_live_tts/upstream-manifest.json'}
        payload[RUNTIME_MANIFEST] = (json.dumps({'schema_version': 1, 'files': runtime_files},
                                               indent=2, sort_keys=True) + '\n').encode()
    result = {'schema_version': 1, 'mode': 'write' if write else 'plan', 'target_app_root': str(target),
              'source_app_root': str(source), 'source_files': source_files,
              'output_root': str(output), 'files': {p: digest(b) for p, b in sorted(payload.items())},
              'manifest_sha256': digest(manifest_raw), 'worker_sha256': digest(transformed),
              'transforms': {'manifest': {'kind': 'three_roots_only', 'source_sha256': digest(original_manifest),
                                          'output_sha256': digest(manifest_raw)},
                             'worker': {'kind': 'MANIFEST_SHA_literal_only', 'source_sha256': digest(worker),
                                        'output_sha256': digest(transformed)}}}
    if natural is not None:
        result['natural_worker_sha256'] = digest(payload['sela_live_tts/natural_worker.py'])
        result['runtime_manifest_sha256'] = digest(payload[RUNTIME_MANIFEST])
        result['transforms']['natural_worker'] = {
            'kind': 'MANIFEST_SHA_literal_only', 'source_sha256': digest(natural),
            'output_sha256': result['natural_worker_sha256']}
        result['transforms']['runtime_manifest'] = {
            'kind': 'generated_exhaustive_relative_inventory', 'source_sha256': None,
            'output_sha256': result['runtime_manifest_sha256']}
    if write:
        output.mkdir()
        for relative, raw in payload.items():
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (output / 'LIVE-RUNTIME-PACKAGE.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-app-root', required=True)
    parser.add_argument('--target-app-root', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--expected-worker-sha256', help='Optional external review binding for the source worker')
    parser.add_argument('--expected-natural-worker-sha256', help='Optional external review binding for the source natural worker')
    parser.add_argument('--write', action='store_true', help='Create NEW staging payload; default only plans')
    args = parser.parse_args(argv)
    try:
        result = build_runtime(args.source_app_root, args.target_app_root, args.output_root,
                               write=args.write, expected_worker_sha256=args.expected_worker_sha256,
                               expected_natural_worker_sha256=args.expected_natural_worker_sha256)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
