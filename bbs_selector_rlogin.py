import os
import re
import select
import socket
import threading

# Enter Your BBSes below - Name, Domain/IP, Port number
BBS_LIST = [
    ("A-Net Online Main Synchronet BBS", "a-net-online.lol", 513),
    ("A-Net Online Mystic BBS", "mystic-anet.online", 513),
    ("A-Net Online bEtA BBS", "x.a-net.online", 5513),
    ("A-Net Online DR0ID BBS", "droid.a-net.online", 5513),
]

# Listen address and port
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 1339

HANDSHAKE_TIMEOUT = 15
CHOICE_TIMEOUT = 120
CONNECT_TIMEOUT = 10
RELAY_BUFSIZE = 8192
DEFAULT_TERM = "syncterm/115200"

# This is the ansi that is shown when a caller connects
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WELCOME_FILE = os.path.join(SCRIPT_DIR, "welcomerlog.ans")


def send_line(sock, line):
    try:
        sock.sendall((line + "\r\n").encode("cp437", errors="replace"))
    except Exception:
        pass


def show_menu(sock):
    try:
        with open(WELCOME_FILE, "rb") as f:
            sock.sendall(f.read())
    except Exception as e:
        send_line(sock, "Welcome to A-Net Online! (Rlogin Connection)")
        send_line(sock, f"(Could not display welcomerlog.ans: {e})")


def handle_rlogin_handshake(client_sock):
    """Read the inbound rlogin handshake.

    Per the BBS rlogin convention used by SyncTerm/Synchronet, the four NUL-
    terminated fields are:
        0x00 client-user(\"password\") server-user(\"username\") term/speed
    Returns (password, username, term_type) or (None, None, None).
    """
    client_sock.settimeout(HANDSHAKE_TIMEOUT)
    try:
        initial = client_sock.recv(1)
        if initial != b"\x00":
            return None, None, None

        strings = []
        current = bytearray()
        while len(strings) < 3:
            byte = client_sock.recv(1)
            if not byte:
                return None, None, None
            if byte == b"\x00":
                strings.append(bytes(current))
                current = bytearray()
            else:
                current += byte
                if len(current) > 256:
                    return None, None, None

        password = strings[0].decode("utf-8", errors="ignore")
        username = strings[1].decode("utf-8", errors="ignore")
        term_type = strings[2].decode("utf-8", errors="ignore") or DEFAULT_TERM
        return password, username, term_type
    except socket.timeout:
        return None, None, None
    except Exception as e:
        print(f"[rlogin] handshake error: {e}")
        return None, None, None
    finally:
        try:
            client_sock.settimeout(None)
        except Exception:
            pass


def send_rlogin_handshake(sock, password, username, term_type):
    """Send an outbound rlogin handshake to the upstream BBS.

    Mirrors the same field order we received so the upstream sees the
    real credentials/terminal instead of synthesized ones.
    """
    if not term_type:
        term_type = DEFAULT_TERM
    try:
        handshake = (
            b"\x00"
            + password.encode("utf-8", errors="replace") + b"\x00"
            + username.encode("utf-8", errors="replace") + b"\x00"
            + term_type.encode("utf-8", errors="replace") + b"\x00"
        )
        sock.sendall(handshake)
        return True
    except Exception as e:
        print(f"[rlogin] upstream handshake error: {e}")
        return False


def read_choice(sock):
    sock.settimeout(CHOICE_TIMEOUT)
    buf = bytearray()
    try:
        while True:
            data = sock.recv(1)
            if not data:
                return None
            b = data[0]
            try:
                sock.sendall(data)
            except Exception:
                return None
            if b in (0x0D, 0x0A):
                return bytes(buf).decode("utf-8", errors="ignore")
            if 0x20 <= b < 0x7F and len(buf) < 16:
                buf.append(b)
    except socket.timeout:
        return None
    finally:
        try:
            sock.settimeout(None)
        except Exception:
            pass


def relay(a, b):
    a.setblocking(True)
    b.setblocking(True)
    a.settimeout(None)
    b.settimeout(None)
    socks = [a, b]
    while True:
        try:
            r, _, _ = select.select(socks, [], [])
        except (OSError, ValueError):
            return
        for s in r:
            try:
                data = s.recv(RELAY_BUFSIZE)
            except OSError:
                return
            if not data:
                return
            other = b if s is a else a
            try:
                other.sendall(data)
            except OSError:
                return


def handle_client(client_sock, client_addr):
    print(f"[rlogin] {client_addr} connected")
    bbs_sock = None
    try:
        password, username, term_type = handle_rlogin_handshake(client_sock)
        if username is None or not username.strip():
            send_line(client_sock, "Invalid rlogin handshake. Disconnecting...")
            return

        show_menu(client_sock)
        try:
            client_sock.sendall(b"Enter your choice: ")
        except Exception:
            return

        choice_str = read_choice(client_sock)
        if choice_str is None:
            send_line(client_sock, "No valid selection entered. Disconnecting...")
            return

        digits = re.sub(r"[^\d]", "", choice_str)
        if not digits:
            send_line(client_sock, "No valid selection entered. Disconnecting...")
            return
        try:
            choice_num = int(digits)
        except ValueError:
            send_line(client_sock, "Invalid selection. Disconnecting...")
            return

        if choice_num < 1 or choice_num > len(BBS_LIST) + 1:
            send_line(client_sock, "Invalid selection. Disconnecting...")
            return

        if choice_num == len(BBS_LIST) + 1:
            send_line(client_sock, "Thank you for visiting! Goodbye.")
            return

        name, host, port = BBS_LIST[choice_num - 1]
        send_line(client_sock, f"Connecting you to {name} via Rlogin...")
        send_line(client_sock, "")
        print(f"[rlogin] {client_addr} ({username}) -> {name} ({host}:{port})")

        try:
            bbs_sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except Exception as e:
            send_line(client_sock, f"Could not connect to {host}:{port}")
            send_line(client_sock, f"Error: {e}")
            return

        if not send_rlogin_handshake(bbs_sock, password, username, term_type):
            send_line(client_sock, "Failed to establish rlogin handshake with target BBS.")
            return

        relay(client_sock, bbs_sock)

    except Exception as e:
        print(f"[rlogin] {client_addr} error: {e}")
    finally:
        if bbs_sock is not None:
            try:
                bbs_sock.close()
            except Exception:
                pass
        try:
            client_sock.close()
        except Exception:
            pass
        print(f"[rlogin] {client_addr} disconnected")


def main():
    print(f"BBS Selector (Rlogin) listening on {LISTEN_HOST}:{LISTEN_PORT}")
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((LISTEN_HOST, LISTEN_PORT))
    server_sock.listen(10)

    try:
        while True:
            client_sock, client_addr = server_sock.accept()
            t = threading.Thread(target=handle_client, args=(client_sock, client_addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()
