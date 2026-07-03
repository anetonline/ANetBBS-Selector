import os
import re
import select
import socket
import threading

# Enter Your BBSes below - Name, Domain/IP, Port number
BBS_LIST = [
    ("A-Net Online Main Synchronet BBS", "a-net.online", 23),
    ("A-Net Online Mystic BBS", "mystic-anet.online", 23),
    ("A-Net Online Spitfire BBS", "sf.a-net.online", 2323),
    ("A-Net Online ANetBBS", "bbs.a-net.fyi", 2233),
]

# Listen address and port
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 1337

CHOICE_TIMEOUT = 120
CONNECT_TIMEOUT = 10
RELAY_BUFSIZE = 8192

# This is the ansi that is shown when a caller connects
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WELCOME_FILE = os.path.join(SCRIPT_DIR, "welcome.ans")

# Telnet protocol constants
IAC  = 0xFF
DONT = 0xFE
DO   = 0xFD
WONT = 0xFC
WILL = 0xFB
SB   = 0xFA
SE   = 0xF0
OPT_BINARY = 0x00
OPT_ECHO   = 0x01
OPT_SGA    = 0x03


def send_initial_telnet_options(sock):
    # Negotiate 8-bit binary in both directions, suppress GA, server echoes.
    # Required so 0xFF bytes in ANSI/CP437 and ZMODEM transfers pass through
    # without being mistaken for Telnet IAC framing.
    try:
        sock.sendall(bytes([
            IAC, WILL, OPT_BINARY,
            IAC, DO,   OPT_BINARY,
            IAC, WILL, OPT_SGA,
            IAC, DO,   OPT_SGA,
            IAC, WILL, OPT_ECHO,
        ]))
    except Exception:
        pass


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
        send_line(sock, "Welcome to A-Net Online!")
        send_line(sock, f"(Could not display welcome.ans: {e})")


class TelnetReader:
    """Reads one data byte at a time, transparently consuming IAC sequences."""

    def __init__(self, sock):
        self.sock = sock

    def _recv1(self):
        b = self.sock.recv(1)
        if not b:
            return None
        return b[0]

    def read_byte(self):
        while True:
            b = self._recv1()
            if b is None:
                return None
            if b != IAC:
                return b
            b2 = self._recv1()
            if b2 is None:
                return None
            if b2 == IAC:
                return IAC  # escaped data byte
            if b2 in (WILL, WONT, DO, DONT):
                if self._recv1() is None:
                    return None
                continue
            if b2 == SB:
                prev = None
                while True:
                    nb = self._recv1()
                    if nb is None:
                        return None
                    if prev == IAC and nb == SE:
                        break
                    prev = nb
                continue
            # Other 2-byte commands (NOP, AYT, etc.) -- already consumed.
            continue


def read_choice(sock):
    sock.settimeout(CHOICE_TIMEOUT)
    reader = TelnetReader(sock)
    buf = bytearray()
    try:
        while True:
            b = reader.read_byte()
            if b is None:
                return None
            try:
                sock.sendall(bytes([b]))
            except Exception:
                return None
            if b in (0x0D, 0x0A):
                return bytes(buf).decode("utf-8", errors="ignore")
            if 0x20 <= b < 0x7F and len(buf) < 16:
                buf.append(b)
    except socket.timeout:
        return None
    finally:
        sock.settimeout(None)


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
    print(f"[telnet] {client_addr} connected")
    bbs_sock = None
    try:
        send_initial_telnet_options(client_sock)
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
        send_line(client_sock, f"Connecting you to {name}...")
        send_line(client_sock, "")
        print(f"[telnet] {client_addr} -> {name} ({host}:{port})")

        try:
            bbs_sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except Exception as e:
            send_line(client_sock, f"Could not connect to {host}:{port}.")
            send_line(client_sock, f"Error: {e}")
            return

        relay(client_sock, bbs_sock)

    except Exception as e:
        print(f"[telnet] {client_addr} error: {e}")
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
        print(f"[telnet] {client_addr} disconnected")


def main():
    print(f"BBS Selector (Telnet) listening on {LISTEN_HOST}:{LISTEN_PORT}")
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
