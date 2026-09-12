"""管理brokerのフレーム境界とpeer認証境界を検査する。"""

from pathlib import Path
import socket
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import authority
from gah.authority import (  # noqa: E402
    AuthorityError,
    MAX_FRAME,
    error_response,
    handle_connection,
    peer_credentials,
    receive_frame,
    send_frame,
)
from gah.contracts import ContractError  # noqa: E402
from gah.wire import canonical_bytes  # noqa: E402


def _framed(body: bytes, *, trailing: bytes = b"") -> bytes:
    return struct.pack("!I", len(body)) + body + trailing


def _send_and_close(connection, data: bytes) -> None:
    connection.sendall(data)
    connection.shutdown(socket.SHUT_WR)


class _Peer:
    def __init__(self, uid=12001, gid=12001, *, family=None, kind=None, raw=None):
        self.family = getattr(socket, "AF_UNIX", 1) if family is None else family
        self.type = socket.SOCK_STREAM if kind is None else kind
        self._raw = (struct.pack("=iII", 7, uid, gid) if raw is None else raw)

    def getsockopt(self, _level, _option, _length):
        return self._raw


class FrameTests(unittest.TestCase):
    def test_partial_header_and_body_is_reassembled(self):
        left, right = socket.socketpair()
        try:
            body = canonical_bytes({"schema_version": 1, "ok": True})
            wire = _framed(body)
            for part in (wire[:2], wire[2:4], wire[4:5], wire[5:]):
                right.sendall(part)
            right.shutdown(socket.SHUT_WR)
            self.assertEqual(receive_frame(left, timeout=1), {"schema_version": 1, "ok": True})
        finally:
            left.close()
            right.close()

    def test_eof_before_header_is_a_fixed_reason(self):
        left, right = socket.socketpair()
        try:
            _send_and_close(right, b"\x00\x02")
            with self.assertRaisesRegex(AuthorityError, "^INCOMPLETE_FRAME$"):
                receive_frame(left, timeout=1)
        finally:
            left.close()
            right.close()

    def test_frame_length_bounds_are_rejected(self):
        for size in (0, MAX_FRAME + 1):
            left, right = socket.socketpair()
            try:
                _send_and_close(right, struct.pack("!I", size))
                with self.subTest(size=size), self.assertRaisesRegex(AuthorityError, "^FRAME_SIZE$"):
                    receive_frame(left, timeout=1)
            finally:
                left.close()
                right.close()

    def test_trailing_bytes_are_rejected(self):
        left, right = socket.socketpair()
        try:
            _send_and_close(right, _framed(b"{}", trailing=b"x"))
            with self.assertRaisesRegex(AuthorityError, "^TRAILING_DATA$"):
                receive_frame(left, timeout=1)
        finally:
            left.close()
            right.close()

    def test_bom_and_duplicate_keys_are_contract_errors(self):
        cases = ((b"\xef\xbb\xbf{}", "INVALID_JSON"), (b'{"x":1,"x":2}', "DUPLICATE_KEY"))
        for body, reason in cases:
            left, right = socket.socketpair()
            try:
                _send_and_close(right, _framed(body))
                with self.subTest(reason=reason), self.assertRaisesRegex(ContractError, f"^{reason}$"):
                    receive_frame(left, timeout=1)
            finally:
                left.close()
                right.close()

    def test_send_frame_round_trip(self):
        left, right = socket.socketpair()
        try:
            send_frame(right, {"ok": True})
            self.assertEqual(receive_frame(left, timeout=1), {"ok": True})
        finally:
            left.close()
            right.close()

    def test_send_frame_rejects_oversized_canonical_body(self):
        class NoIO:
            def settimeout(self, _value):
                self.called = True

        connection = NoIO()
        with self.assertRaisesRegex(AuthorityError, "^FRAME_SIZE$"):
            send_frame(connection, {"body": "x" * MAX_FRAME})
        self.assertFalse(hasattr(connection, "called"))


class PeerCredentialTests(unittest.TestCase):
    def test_allowed_uid_and_gid_are_returned(self):
        with patch.object(authority.sys, "platform", "linux"), patch.object(
            authority.socket, "AF_UNIX", 1, create=True
        ), patch.object(authority.socket, "SO_PEERCRED", 17, create=True):
            self.assertEqual(peer_credentials(_Peer(12001, 12001)), (12001, 12001))

    def test_unknown_uid_and_mismatched_gid_are_rejected(self):
        cases = ((999, 999), (12001, 999), (999, 12001))
        for uid, gid in cases:
            with patch.object(authority.sys, "platform", "linux"), patch.object(
                authority.socket, "AF_UNIX", 1, create=True
            ), patch.object(authority.socket, "SO_PEERCRED", 17, create=True):
                with self.subTest(uid=uid, gid=gid), self.assertRaisesRegex(
                    AuthorityError, "^AUTHENTICATION_REQUIRED$"
                ):
                    peer_credentials(_Peer(uid, gid))

    def test_short_peer_credential_value_is_unavailable(self):
        with patch.object(authority.sys, "platform", "linux"), patch.object(
            authority.socket, "AF_UNIX", 1, create=True
        ), patch.object(authority.socket, "SO_PEERCRED", 17, create=True):
            with self.assertRaisesRegex(AuthorityError, "^PEER_AUTH_UNAVAILABLE$"):
                peer_credentials(_Peer(raw=b"\x00" * 4))

    def test_non_unix_or_wrong_socket_type_is_unavailable(self):
        with patch.object(authority.sys, "platform", "linux"), patch.object(
            authority.socket, "AF_UNIX", 1, create=True
        ), patch.object(authority.socket, "AF_INET", 2, create=True), patch.object(
            authority.socket, "SO_PEERCRED", 17, create=True
        ):
            for peer in (_Peer(family=2), _Peer(kind=socket.SOCK_DGRAM)):
                with self.subTest(peer=peer), self.assertRaisesRegex(
                    AuthorityError, "^PEER_AUTH_UNAVAILABLE$"
                ):
                    peer_credentials(peer)


class HandleConnectionTests(unittest.TestCase):
    class Store:
        def __init__(self):
            self.calls = []

        def dispatch(self, uid, gid, request):
            self.calls.append((uid, gid, request))
            actor = "manager" if (uid, gid) == (12001, 12001) else "unexpected"
            return {"schema_version": 1, "kind": "fake", "actor": actor, "ci_eligible": False}

    def _run(self, payload: bytes, *, credentials=(12001, 12001), store=None):
        client, server = socket.socketpair()
        store = self.Store() if store is None else store
        try:
            _send_and_close(client, payload)
            with patch("gah.authority.peer_credentials", return_value=credentials):
                handle_connection(server, store)
            return receive_frame(client, timeout=1), store
        finally:
            client.close()
            server.close()

    def test_request_actor_self_claim_does_not_change_peer_derived_identity(self):
        request = {
            "schema_version": 1,
            "action": "current",
            "request_id": "request-1",
            "actor": "candidate",
            "context": "candidate-context",
        }
        response, store = self._run(_framed(canonical_bytes(request)))
        self.assertEqual(response["actor"], "manager")
        self.assertEqual(store.calls, [(12001, 12001, request)])

    def test_authentication_failure_is_returned_without_dispatch(self):
        client, server = socket.socketpair()
        store = self.Store()
        try:
            with patch("gah.authority.peer_credentials", side_effect=AuthorityError("AUTHENTICATION_REQUIRED")):
                handle_connection(server, store)
            response = receive_frame(client, timeout=1)
            self.assertEqual(response, error_response("AUTHENTICATION_REQUIRED"))
            self.assertEqual(store.calls, [])
        finally:
            client.close()
            server.close()

    def test_malformed_transport_and_json_reasons_are_fixed(self):
        cases = (
            (b"\x00\x02", "INCOMPLETE_FRAME"),
            (_framed(b"{}", trailing=b"x"), "TRAILING_DATA"),
            (_framed(b"\xef\xbb\xbf{}"), "INVALID_JSON"),
            (_framed(b'{"x":1,"x":2}'), "DUPLICATE_KEY"),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                response, store = self._run(payload)
                self.assertEqual(response, error_response(reason))
                self.assertEqual(store.calls, [])

    def test_unexpected_store_error_is_fixed_and_next_connection_is_served(self):
        class FailingOnceStore(self.Store):
            def dispatch(self, uid, gid, request):
                self.calls.append((uid, gid, request))
                if len(self.calls) == 1:
                    raise RuntimeError("内部実装詳細を応答へ出さない")
                return {"schema_version": 1, "kind": "fake", "actor": "manager", "ci_eligible": False}

        payload = _framed(canonical_bytes({"schema_version": 1, "request_id": "request-1"}))
        store = FailingOnceStore()
        first, _ = self._run(payload, store=store)
        second, _ = self._run(payload, store=store)
        self.assertEqual(first, error_response("INTERNAL_FAILURE"))
        self.assertEqual(second["kind"], "fake")
        self.assertEqual(len(store.calls), 2)


if __name__ == "__main__":
    unittest.main()
