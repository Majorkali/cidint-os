"""Client side of the cidint-authd protocol: one JSON request per
connection, one JSON reply."""
import json
import socket

SOCKET = "/run/cidint-auth/socket"


class AuthError(Exception):
    pass


def request(op: str, timeout: float = 30.0, **fields) -> dict:
    """Send one request; return the reply. Raises AuthError when the
    service is unreachable or refuses."""
    msg = dict(fields, op=op)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCKET)
            s.sendall((json.dumps(msg) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError as e:
        raise AuthError(f"the system service is not reachable ({e.strerror or e})") from e
    try:
        reply = json.loads(data.decode() or "{}")
    except ValueError as e:
        raise AuthError("unreadable reply from the system service") from e
    if not reply.get("ok"):
        raise AuthError(reply.get("error", "refused"))
    return reply
