"""固定runの送信意図と観測を、排他下で不変の段階記録へ保存する。"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
import uuid

from gah.contracts import MAX_DOCUMENT_BYTES, decode_document, require_id, require_object
from gah.wire import canonical_bytes


class CheckpointError(ValueError):
    pass


def _plain_directory(path):
    """既存の親も含め、symlink/reparse pointを保存先として使わない。"""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise CheckpointError('CHECKPOINT_PATH_INVALID')
    return path


class Checkpoint:
    """呼出側が同runのoperation_lockを保持する。hashは認証を代替しない。"""
    def __init__(self, folder):
        self.folder = _plain_directory(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        require_id(key)
        _plain_directory(self.folder)
        return self.folder / (hashlib.sha256(key.encode('utf-8')).hexdigest() + '.json')

    def get(self, key):
        path = self._path(key)
        if not path.exists() and not path.is_symlink():
            return None
        _plain_directory(path)
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_DOCUMENT_BYTES + 1)
            value = decode_document(raw)
            require_object(value, {'schema_version', 'key', 'payload', 'digest'})
            if (type(value['schema_version']) is not int or value['schema_version'] != 1
                    or value['key'] != key or type(value['payload']) is not dict
                    or hashlib.sha256(canonical_bytes(value['payload'])).hexdigest() != value['digest']
                    or canonical_bytes(value) != raw):
                raise CheckpointError('CHECKPOINT_CORRUPT')
            return deepcopy(value['payload'])
        except (OSError, ValueError, TypeError, KeyError):
            raise CheckpointError('CHECKPOINT_CORRUPT') from None

    def put(self, key, payload):
        path = self._path(key)
        if type(payload) is not dict:
            raise CheckpointError('CHECKPOINT_VALUE_INVALID')
        frozen = decode_document(canonical_bytes(payload))
        value = {'schema_version': 1, 'key': key, 'payload': frozen,
            'digest': hashlib.sha256(canonical_bytes(frozen)).hexdigest()}
        raw = canonical_bytes(value)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise CheckpointError('CHECKPOINT_TOO_LARGE')
        previous = self.get(key)
        if previous is not None:
            if canonical_bytes(previous) != canonical_bytes(frozen):
                raise CheckpointError('CHECKPOINT_CONFLICT')
            return previous
        temporary = self.folder / ('pending-' + uuid.uuid4().hex)
        try:
            with temporary.open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # runのOS lockの内側で、同一keyが未存在の場合だけ公開する。
            if path.exists() or path.is_symlink():
                raise CheckpointError('CHECKPOINT_CONFLICT')
            os.replace(temporary, path)
            if os.name != 'nt':
                fd = os.open(self.folder, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if temporary.exists() and temporary.parent == self.folder:
                temporary.unlink()
        return deepcopy(frozen)
