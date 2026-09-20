"""Read-only fixed-volume statvfs observation for an owned AuthorityRuntime broker."""
from __future__ import annotations

import json, re, time
from typing import Any
from gah.contracts import (
    ContractError,
    MAX_INTEGER,
)
from gah.storage_observation import validate_storage_observation as _core_validate
MAX_OUTPUT_BYTES = 4096
_PREFIX = re.compile('gah-authority-[0-9a-f]{32}\\Z')
_IMAGE = re.compile('sha256:[0-9a-f]{64}\\Z')
_CONTAINER = re.compile('[0-9a-f]{64}\\Z')
STATVFS_SCRIPT = ("import json,os\n"
"def scope(path):\n"
" try:\n"
"  s=os.statvfs(path); unit=s.f_frsize or s.f_bsize\n"
"  if type(unit) is not int or unit<=0: return None\n"
"  vals=(s.f_bavail*unit,s.f_bfree*unit,s.f_blocks*unit)\n"
"  if any(type(v) is not int or v<0 or v>9223372036854775807 for v in vals): return None\n"
"  ino=s.f_favail\n"
"  if type(ino) is not int or ino<0 or ino>9223372036854775807: ino=None\n"
"  return {'available_bytes':vals[0],'free_bytes':vals[1],'total_bytes':vals[2],'available_inodes':ino}\n"
" except (OSError,OverflowError,ValueError): return None\n"
"print(json.dumps({'state':scope('/state'),'ipc':scope('/ipc')},separators=(',',':'),sort_keys=True))")

def validate_storage_observation(value: Any) -> dict[str, Any]:
    return _core_validate(value)


def _unknown(reason, *, image=None, prefix=None, container=None, state=None, ipc=None):
    return validate_storage_observation({
        'schema_version': 1,
        'kind': 'gah_authority_storage_observation',
        'status': 'UNKNOWN',
        'source': {'image_id': image, 'prefix': prefix, 'container_id': container},
        'scopes': {'state': state, 'ipc': ipc},
        'checked_at': int(time.time()),
        'checked_at_source': 'host_observed',
        'reason': reason,
        'ci_eligible': False,
    })


def _pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError('duplicate')
        d[k] = v
    return d


def _volume_signature(runtime, name):
    raw = runtime.command(['volume', 'inspect', name], timeout=10, limit=MAX_OUTPUT_BYTES)
    if type(raw) not in (str, bytes):
        raise ValueError('bad volume')
    encoded = raw.encode('utf-8') if type(raw) is str else raw
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError('oversize')
    values = json.loads(encoded.decode('utf-8'), object_pairs_hook=_pairs)
    if type(values) is not list or len(values) != 1 or type(values[0]) is not dict:
        raise ValueError('shape')
    volume = values[0]
    runtime._verify_volume(volume, name)
    labels = volume.get('Labels')
    if type(labels) is not dict or labels.get('org.gah.authority.instance') != runtime.prefix:
        raise ValueError('owner')
    fields = ('Name', 'Driver', 'Scope', 'Options', 'Mountpoint', 'CreatedAt')
    if any((k not in volume for k in fields)):
        raise ValueError('field')
    return (
        tuple((k, json.dumps(volume[k], sort_keys=True, separators=(',', ':'))) for k in fields)
        + (('owner', runtime.prefix),)
    )


def _inspect(runtime, name, image):
    try:
        data = runtime.inspect(name)
    except Exception:
        raise LookupError('broker unavailable') from None
    runtime._verify_config(data, 12000, 'broker')
    if type(data) is not dict or data.get('Name') != '/' + name or data.get('Image') != image:
        raise ValueError('config')
    config, state = (data.get('Config'), data.get('State'))
    if type(config) is not dict or type(state) is not dict:
        raise ValueError('shape')
    labels = config.get('Labels')
    if type(labels) is not dict or labels.get('org.gah.authority.instance') != runtime.prefix:
        raise ValueError('owner')
    ident, started, restart = (data.get('Id'), state.get('StartedAt'), data.get('RestartCount'))
    if (
        type(ident) is not str
        or _CONTAINER.fullmatch(ident) is None
        or type(started) is not str
        or not started
        or type(restart) is not int
        or not 0 <= restart <= MAX_INTEGER
        or state.get('Running') is not True
        or state.get('Status') != 'running'
        or type(state.get('Pid')) is not int
        or state['Pid'] <= 0
    ):
        raise ValueError('not running')
    return (ident, restart, started)


def _read_scopes(raw):
    if type(raw) not in (str, bytes):
        raise ValueError('type')
    data = raw.encode('utf-8') if type(raw) is str else raw
    if len(data) > MAX_OUTPUT_BYTES:
        raise ValueError('oversize')
    doc = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs)
    if type(doc) is not dict or set(doc) != {'state', 'ipc'}:
        raise ValueError('shape')
    scopes = {}
    missing = False
    fields = {'available_bytes', 'free_bytes', 'total_bytes', 'available_inodes'}
    for name in ('state', 'ipc'):
        item = doc[name]
        if item is None:
            scopes[name] = None
            missing = True
            continue
        if type(item) is not dict or set(item) != fields:
            raise ValueError('scope')
        if any((v is not None and (type(v) is not int or not 0 <= v <= MAX_INTEGER) for v in item.values())):
            raise ValueError('metric')
        a, f, t = (item[k] for k in ('available_bytes', 'free_bytes', 'total_bytes'))
        if a is None or f is None or t is None or (item['available_inodes'] is None):
            missing = True
        if a is not None and f is not None and (a > f) or (f is not None and t is not None and (f > t)):
            raise ValueError('order')
        scopes[name] = item
    return (scopes, missing)


def observe_authority_storage(runtime: Any) -> dict[str, Any]:
    prefix = getattr(runtime, 'prefix', None)
    state = getattr(runtime, 'state', None)
    lock = getattr(runtime, 'lock', None)
    image = lock.get('image_id') if type(lock) is dict else None
    if (
        type(prefix) is not str
        or _PREFIX.fullmatch(prefix) is None
        or type(state) is not dict
        or state.get('prefix') != prefix
        or type(state.get('containers')) is not list
        or prefix + '-broker' not in state['containers']
        or type(image) is not str
        or _IMAGE.fullmatch(image) is None
        or not all(callable(getattr(runtime, k, None)) for k in ('inspect', 'command', '_verify_config', '_verify_volume'))
    ):
        return _unknown(
            'RUNTIME_INVALID',
            image=image if type(image) is str and _IMAGE.fullmatch(image) else None,
            prefix=prefix if type(prefix) is str and _PREFIX.fullmatch(prefix) else None,
        )
    name = prefix + '-broker'
    try:
        before = _inspect(runtime, name, image)
    except LookupError:
        return _unknown('BROKER_UNAVAILABLE', image=image, prefix=prefix)
    except Exception:
        return _unknown('CONFIG_MISMATCH', image=image, prefix=prefix)
    container = before[0]
    names = {'state': prefix + '-state', 'ipc': prefix + '-ipc'}
    try:
        signatures = {k: _volume_signature(runtime, v) for k, v in names.items()}
    except Exception:
        return _unknown('VOLUME_MISMATCH', image=image, prefix=prefix, container=container)
    try:
        raw = runtime.command(
            ['container', 'exec', name, 'python3', '-c', STATVFS_SCRIPT],
            timeout=10,
            limit=MAX_OUTPUT_BYTES,
        )
    except Exception:
        return _unknown('COMMAND_FAILED', image=image, prefix=prefix, container=container)
    try:
        scopes, missing = _read_scopes(raw)
    except Exception:
        return _unknown('OBSERVATION_INVALID', image=image, prefix=prefix, container=container)
    try:
        after = _inspect(runtime, name, image)
    except Exception:
        return _unknown(
            'BROKER_RESTARTED', image=image, prefix=prefix, container=container,
            state=scopes['state'], ipc=scopes['ipc'],
        )
    if after != before:
        return _unknown(
            'BROKER_RESTARTED', image=image, prefix=prefix, container=container,
            state=scopes['state'], ipc=scopes['ipc'],
        )
    try:
        if any((_volume_signature(runtime, v) != signatures[k] for k, v in names.items())):
            return _unknown(
                'VOLUME_MISMATCH', image=image, prefix=prefix, container=container,
                state=scopes['state'], ipc=scopes['ipc'],
            )
    except Exception:
        return _unknown(
                'VOLUME_MISMATCH', image=image, prefix=prefix, container=container,
                state=scopes['state'], ipc=scopes['ipc'],
            )
    result = {
        'schema_version': 1,
        'kind': 'gah_authority_storage_observation',
        'status': 'UNKNOWN' if missing else 'OBSERVED',
        'source': {'image_id': image, 'prefix': prefix, 'container_id': container},
        'scopes': scopes,
        'checked_at': int(time.time()),
        'checked_at_source': 'host_observed',
        'reason': 'OBSERVATION_MISSING' if missing else None,
        'ci_eligible': False,
    }
    try:
        return validate_storage_observation(result)
    except ContractError:
        return _unknown(
            'OBSERVATION_INVALID', image=image, prefix=prefix, container=container,
            state=scopes['state'], ipc=scopes['ipc'],
        )

__all__ = ['MAX_OUTPUT_BYTES', 'STATVFS_SCRIPT', 'observe_authority_storage', 'validate_storage_observation']
