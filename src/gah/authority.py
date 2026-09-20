"""Linux Unix socketのpeer credentialからのみ管理APIの主体を決める。"""
import errno
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time

from .contracts import ContractError, decode_document
from .wire import canonical_bytes

MAX_FRAME = 1024 * 1024
IO_TIMEOUT = 5
RESPONSE_TIMEOUT = 30
IDENTITIES = {12001, 12002, 12003, 12004}


class AuthorityError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _read_exact(connection, count, end):
    body = bytearray()
    while len(body) < count:
        left = end - time.monotonic()
        if left <= 0:
            raise AuthorityError("REQUEST_TIMEOUT")
        connection.settimeout(left)
        try:
            block = connection.recv(min(count - len(body), 65536))
        except (TimeoutError, socket.timeout):
            raise AuthorityError("REQUEST_TIMEOUT") from None
        if not block:
            raise AuthorityError("INCOMPLETE_FRAME")
        body.extend(block)
    return bytes(body)


def receive_frame(connection, *, timeout=IO_TIMEOUT):
    end = time.monotonic() + timeout
    size = struct.unpack("!I", _read_exact(connection, 4, end))[0]
    if not 1 <= size <= MAX_FRAME:
        raise AuthorityError("FRAME_SIZE")
    body = _read_exact(connection, size, end)
    left = end - time.monotonic()
    if left <= 0:
        raise AuthorityError("REQUEST_TIMEOUT")
    connection.settimeout(left)
    try:
        trailing = connection.recv(1)
    except (TimeoutError, socket.timeout):
        raise AuthorityError("REQUEST_TIMEOUT") from None
    if trailing:
        raise AuthorityError("TRAILING_DATA")
    return decode_document(body)


def send_frame(connection, value):
    body = canonical_bytes(value)
    if not 1 <= len(body) <= MAX_FRAME:
        raise AuthorityError("FRAME_SIZE")
    connection.settimeout(IO_TIMEOUT)
    connection.sendall(struct.pack("!I", len(body)) + body)
    connection.shutdown(socket.SHUT_WR)


def peer_credentials(connection):
    if not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"):
        raise AuthorityError("PEER_AUTH_UNAVAILABLE")
    if connection.family != socket.AF_UNIX or connection.type & socket.SOCK_STREAM != socket.SOCK_STREAM:
        raise AuthorityError("PEER_AUTH_UNAVAILABLE")
    try:
        value = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("=iII"))
        pid, uid, gid = struct.unpack("=iII", value)
    except (OSError, struct.error, TypeError):
        raise AuthorityError("PEER_AUTH_UNAVAILABLE") from None
    # 別PID namespaceのpeer PIDは0の場合がある。認証にはUID/GIDを使う。
    if uid not in IDENTITIES or gid != uid:
        raise AuthorityError("AUTHENTICATION_REQUIRED")
    return uid, gid


def error_response(code):
    return {"schema_version": 1, "kind": "authority_error", "reason": code, "ci_eligible": False}


def handle_connection(connection, store):
    from .adoption import AdoptionError
    try:
        uid, gid = peer_credentials(connection)
        request = receive_frame(connection)
        response = store.dispatch(uid, gid, request)
    except (AuthorityError, ContractError, AdoptionError) as error:
        response = error_response(error.code)
    except OSError:
        response = error_response("TRANSPORT_FAILURE")
    except Exception:
        response = error_response("INTERNAL_FAILURE")
    try:
        send_frame(connection, response)
    except (AuthorityError, OSError, ValueError, TypeError):
        pass


def _check_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise AuthorityError("UNTRUSTED_SOCKET_DIRECTORY")


def _database_extension(database_mode):
    """Select the legacy default or an exact explicitly requested schema mode."""
    if type(database_mode) is not str:
        raise AuthorityError("DATABASE_MODE_INVALID")
    if database_mode == "default":
        from .evaluation_authority import EvaluationExtension
        return EvaluationExtension()
    if database_mode == "partitioned-v6":
        from .partitioned_run_authority import PartitionedRunEvaluationExtension
        return PartitionedRunEvaluationExtension()
    if database_mode == "partitioned-v7":
        from .partitioned_corpus_authority import PartitionedCorpusEvaluationExtension
        return PartitionedCorpusEvaluationExtension()
    raise AuthorityError("DATABASE_MODE_INVALID")


def serve(socket_path, db_path, *, database_mode="default"):
    from .adoption import AdoptionStore
    extension = _database_extension(database_mode)
    if not sys.platform.startswith("linux") or os.geteuid() != 12000 or os.getegid() != 12000:
        raise AuthorityError("BROKER_IDENTITY_MISMATCH")
    path = Path(socket_path)
    _check_directory(path.parent)
    _check_directory(Path(db_path).parent)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise AuthorityError("UNTRUSTED_SOCKET")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            try:
                probe.connect(str(path))
            except OSError as error:
                if error.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                    raise AuthorityError("SOCKET_STATE_UNKNOWN") from None
            else:
                raise AuthorityError("BROKER_ALREADY_RUNNING")
        path.unlink()
    os.umask(0o077)
    # AdoptionStore creates a blank DB using this exact extension. Existing DB version
    # or digest mismatches fail closed; serve never performs a schema migration.
    with AdoptionStore(db_path, extension=extension) as store, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        os.chmod(path, 0o666)
        server.listen(8)
        while True:
            connection, _ = server.accept()
            with connection:
                handle_connection(connection, store)


def call(socket_path, request):
    """requestにはactor/contextを加えない。呼出し元のOS identityで認証される。"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(IO_TIMEOUT)
        connection.connect(str(socket_path))
        send_frame(connection, request)
        return receive_frame(connection, timeout=RESPONSE_TIMEOUT)
