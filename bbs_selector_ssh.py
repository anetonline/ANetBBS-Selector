import logging
import os
import re
import select
import socket
import threading

import paramiko

# Enter Your BBSes below - Name, Domain/IP, Port number
BBS_LIST = [
    ("A-Net Online Main Synchronet BBS", "a-net.online", 22),
    ("A-Net Online Mystic BBS", "mystic-anet.online", 22),
    ("A-Net Online ANetBBS", "bbs.a-net.fyi", 2234),
    ("A-Net Online DR0ID MATRIX BBS", "droid.a-net.online", 2222),
]

# Listen address and port
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 1338

CHOICE_TIMEOUT = 120
CONNECT_TIMEOUT = 15
SSH_BANNER_TIMEOUT = 20
RELAY_BUFSIZE = 8192

# This is the ansi that is shown when a caller connects
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WELCOME_FILE = os.path.join(SCRIPT_DIR, "welcomessh.ans")

# DH "group exchange" primes. paramiko strips diffie-hellman-group-exchange-*
# KEX from its offered list unless a modulus pack is loaded -- and that
# algorithm happens to be the only KEX SyncTerm 1.10+ has in common with
# paramiko 5.x. /etc/ssh/moduli is shipped by openssh-server.
MODULI_CANDIDATES = (
    "/etc/ssh/moduli",
    "/etc/moduli",
)

# Host keys are tried in this order. Modern SyncTerm (cryptlib-based) prefers
# Ed25519 over RSA; loading all that exist lets the client pick the best one.
# Generate an Ed25519 host key with:
#   ssh-keygen -t ed25519 -f ssh_host_ed25519_key -N ""
HOST_KEY_CANDIDATES = [
    ("ssh_host_ed25519_key", paramiko.Ed25519Key),
    ("ssh_host_ecdsa_key",   paramiko.ECDSAKey),
    ("ssh_host_rsa_key",     paramiko.RSAKey),
]

# Only disable algorithms that strict modern clients (SyncTerm/cryptlib) refuse
# OUTRIGHT. The big one is "ssh-rsa" -- the SHA-1 RSA host-key signature.
# rsa-sha2-256/512 stay enabled. KEX/cipher/MAC are widened below instead of
# narrowed here, because cryptlib's KEX support skews older.
DISABLED_ALGORITHMS = {
    "keys": ["ssh-rsa"],
    "pubkeys": ["ssh-rsa"],
}

# Preferred algorithm wishlists, ordered best-first. The configure_security()
# helper sets these as paramiko's preferred lists, falling back gracefully if
# any entry isn't recognised by the installed paramiko build. SHA-1 KEX and
# CBC ciphers are kept as last-resort fallbacks so older cryptlib clients
# (SyncTerm) can still find an overlap.
PREFERRED_KEX = (
    "curve25519-sha256",
    "curve25519-sha256@libssh.org",
    "ecdh-sha2-nistp256",
    "ecdh-sha2-nistp384",
    "ecdh-sha2-nistp521",
    "diffie-hellman-group16-sha512",
    "diffie-hellman-group-exchange-sha256",
    "diffie-hellman-group14-sha256",
    "diffie-hellman-group-exchange-sha1",
    "diffie-hellman-group14-sha1",
)

PREFERRED_CIPHERS = (
    "aes128-gcm@openssh.com",
    "aes256-gcm@openssh.com",
    "chacha20-poly1305@openssh.com",
    "aes128-ctr",
    "aes192-ctr",
    "aes256-ctr",
    "aes256-cbc",
    "aes192-cbc",
    "aes128-cbc",
)

PREFERRED_MACS = (
    "hmac-sha2-256-etm@openssh.com",
    "hmac-sha2-512-etm@openssh.com",
    "hmac-sha2-256",
    "hmac-sha2-512",
    "hmac-sha1",
)


def configure_security(transport):
    """Widen paramiko's preferred algorithm lists for cryptlib-based clients.

    paramiko 3.x/5.x defaults dropped SHA-1 KEX and CBC ciphers, which leaves
    no overlap with some SyncTerm builds. We re-add them as fallbacks, keeping
    modern algorithms preferred.
    """
    sec = transport.get_security_options()
    for attr, wishlist in (
        ("kex", PREFERRED_KEX),
        ("ciphers", PREFERRED_CIPHERS),
        ("digests", PREFERRED_MACS),
    ):
        # Build the longest prefix of wishlist that paramiko accepts. Each
        # entry that paramiko doesn't recognise raises ValueError; we just
        # skip it and keep going.
        accepted = []
        for algo in wishlist:
            try:
                setattr(sec, attr, tuple(accepted + [algo]))
                accepted.append(algo)
            except (ValueError, TypeError):
                continue
        if accepted:
            print(f"[ssh] offering {attr}: {','.join(accepted)}")

# Set SSH_DEBUG=1 in the environment to get verbose paramiko negotiation logs.
if os.environ.get("SSH_DEBUG"):
    paramiko.util.log_to_file(os.path.join(SCRIPT_DIR, "paramiko_debug.log"),
                              level=logging.DEBUG)


def send_line(chan, line):
    try:
        chan.sendall((line + "\r\n").encode("cp437", errors="replace"))
    except Exception:
        pass


def show_menu(chan):
    try:
        with open(WELCOME_FILE, "rb") as f:
            chan.sendall(f.read())
    except Exception as e:
        send_line(chan, "Welcome to A-Net Online! (SSH Connection)")
        send_line(chan, f"(Could not display welcomessh.ans: {e})")


class SimpleSSHServer(paramiko.ServerInterface):
    """Captures auth creds and PTY parameters so we can forward them upstream."""

    def __init__(self):
        super().__init__()
        self.auth_username = None
        self.auth_password = None
        # PTY defaults; the real values are filled in by check_channel_pty_request
        self.term = "ansi-bbs"
        self.width = 80
        self.height = 25
        self.pixelwidth = 0
        self.pixelheight = 0
        self.shell_event = threading.Event()
        self.remote_session = None
        self._lock = threading.Lock()

    def check_auth_password(self, username, password):
        self.auth_username = username
        self.auth_password = password
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_none(self, username):
        # Some BBS clients try "none" auth first. Accept it so they get a chance
        # to fall through to password auth.
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        try:
            if isinstance(term, (bytes, bytearray)):
                self.term = term.decode("ascii", errors="ignore")
            else:
                self.term = str(term)
        except Exception:
            self.term = "ansi-bbs"
        if not self.term:
            self.term = "ansi-bbs"
        self.width = width or 80
        self.height = height or 25
        self.pixelwidth = pixelwidth or 0
        self.pixelheight = pixelheight or 0
        return True

    def check_channel_shell_request(self, channel):
        self.shell_event.set()
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        self.width = width
        self.height = height
        self.pixelwidth = pixelwidth
        self.pixelheight = pixelheight
        with self._lock:
            rs = self.remote_session
        if rs is not None:
            try:
                rs.resize_pty(
                    width=width,
                    height=height,
                    width_pixels=pixelwidth,
                    height_pixels=pixelheight,
                )
            except Exception:
                pass
        return True

    def set_remote_session(self, rs):
        with self._lock:
            self.remote_session = rs


def read_choice(chan):
    chan.settimeout(CHOICE_TIMEOUT)
    buf = bytearray()
    try:
        while True:
            data = chan.recv(1)
            if not data:
                return None
            b = data[0]
            try:
                chan.sendall(data)
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
            chan.settimeout(None)
        except Exception:
            pass


def relay(client_chan, remote_chan):
    client_chan.setblocking(True)
    remote_chan.setblocking(True)
    chans = [client_chan, remote_chan]
    while True:
        try:
            r, _, _ = select.select(chans, [], [])
        except (OSError, ValueError):
            return
        for c in r:
            try:
                data = c.recv(RELAY_BUFSIZE)
            except Exception:
                return
            if not data:
                return
            other = remote_chan if c is client_chan else client_chan
            try:
                other.sendall(data)
            except Exception:
                return


def load_host_keys():
    loaded = []
    for filename, key_cls in HOST_KEY_CANDIDATES:
        path = os.path.join(SCRIPT_DIR, filename)
        if not os.path.exists(path):
            continue
        try:
            loaded.append((filename, key_cls.from_private_key_file(path)))
        except Exception as e:
            print(f"[ssh] could not load host key {filename}: {e}")
    return loaded


def handle_client(client_sock, client_addr, host_keys):
    print(f"[ssh] {client_addr} connected")
    transport = None
    channel = None
    ssh = None
    try:
        try:
            transport = paramiko.Transport(
                client_sock,
                disabled_algorithms=DISABLED_ALGORITHMS,
            )
        except TypeError:
            # Very old paramiko (<2.6) doesn't support disabled_algorithms.
            transport = paramiko.Transport(client_sock)
        transport.banner_timeout = SSH_BANNER_TIMEOUT
        try:
            transport.set_gss_host(socket.getfqdn(""))
        except Exception:
            pass

        configure_security(transport)

        for filename, key in host_keys:
            try:
                transport.add_server_key(key)
            except Exception as e:
                print(f"[ssh] add_server_key({filename}) failed: {e}")

        server = SimpleSSHServer()
        try:
            transport.start_server(server=server)
        except paramiko.SSHException as e:
            print(f"[ssh] {client_addr} negotiation failed: {e}")
            return

        # Log negotiated algorithms once per session so we can diagnose
        # mismatches without enabling full debug logging. Attribute names
        # differ between paramiko versions; just print what we can find.
        try:
            bits = []
            for attr in ("local_cipher", "local_mac", "host_key_type"):
                v = getattr(transport, attr, None)
                if v:
                    bits.append(f"{attr}={v}")
            if bits:
                print(f"[ssh] {client_addr} negotiated " + " ".join(bits))
        except Exception:
            pass

        channel = transport.accept(20)
        if channel is None:
            print(f"[ssh] {client_addr} no channel opened")
            return

        # Give the client a moment to send pty + shell requests before we
        # send the menu, so we have real terminal dimensions on hand.
        server.shell_event.wait(timeout=10)

        show_menu(channel)
        try:
            channel.sendall(b"Enter your choice: ")
        except Exception:
            return

        choice_str = read_choice(channel)
        if choice_str is None:
            send_line(channel, "No valid selection entered. Disconnecting...")
            return

        digits = re.sub(r"[^\d]", "", choice_str)
        if not digits:
            send_line(channel, "No valid selection entered. Disconnecting...")
            return
        try:
            choice_num = int(digits)
        except ValueError:
            send_line(channel, "Invalid selection. Disconnecting...")
            return

        if choice_num < 1 or choice_num > len(BBS_LIST) + 1:
            send_line(channel, "Invalid selection. Disconnecting...")
            return

        if choice_num == len(BBS_LIST) + 1:
            send_line(channel, "Thank you for visiting! Goodbye.")
            return

        name, host, port = BBS_LIST[choice_num - 1]
        send_line(channel, f"Connecting you to {name} via SSH...")
        send_line(channel, "")
        print(f"[ssh] {client_addr} ({server.auth_username}) -> {name} ({host}:{port}) "
              f"term={server.term} {server.width}x{server.height}")

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=host,
                port=port,
                username=server.auth_username,
                password=server.auth_password,
                allow_agent=False,
                look_for_keys=False,
                timeout=CONNECT_TIMEOUT,
                banner_timeout=SSH_BANNER_TIMEOUT,
                auth_timeout=SSH_BANNER_TIMEOUT,
            )
            upstream_transport = ssh.get_transport()
            upstream_transport.set_keepalive(30)

            remote_session = upstream_transport.open_session()
            remote_session.get_pty(
                term=server.term,
                width=server.width,
                height=server.height,
                width_pixels=server.pixelwidth,
                height_pixels=server.pixelheight,
            )
            remote_session.invoke_shell()
            server.set_remote_session(remote_session)

            relay(channel, remote_session)

        except Exception as e:
            send_line(channel, f"Could not connect to {host}:{port}")
            send_line(channel, f"Error: {e}")
            print(f"[ssh] {client_addr} upstream error: {e}")

    except Exception as e:
        print(f"[ssh] {client_addr} error: {e}")
    finally:
        if ssh is not None:
            try:
                ssh.close()
            except Exception:
                pass
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        try:
            client_sock.close()
        except Exception:
            pass
        print(f"[ssh] {client_addr} disconnected")


def init_modulus_pack():
    """Load DH primes ONCE at startup. Required so paramiko keeps
    diffie-hellman-group-exchange-sha256 in its KEX offer -- SyncTerm 1.10a
    only matches paramiko on that one algorithm."""
    for moduli_path in MODULI_CANDIDATES:
        if not os.path.exists(moduli_path):
            print(f"[ssh] moduli: {moduli_path} not found, skipping")
            continue
        try:
            result = paramiko.Transport.load_server_moduli(moduli_path)
        except Exception as e:
            print(f"[ssh] moduli: load_server_moduli({moduli_path}) raised {e!r}")
            continue
        loaded = paramiko.Transport._modulus_pack
        if result and loaded is not None and getattr(loaded, "pack", None):
            print(f"[ssh] moduli: loaded {moduli_path} "
                  f"({sum(len(v) for v in loaded.pack.values())} primes, "
                  f"sizes={sorted(loaded.pack.keys())})")
            return True
        print(f"[ssh] moduli: load_server_moduli({moduli_path}) returned "
              f"{result}, pack={'<empty>' if loaded is None else 'set-but-empty'}")
    print("[ssh] WARNING: no usable moduli file. SyncTerm and other clients "
          "that only offer diffie-hellman-group-exchange-* KEX will not "
          "connect.")
    return False


def main():
    init_modulus_pack()
    host_keys = load_host_keys()
    if not host_keys:
        print("[ssh] FATAL: no host keys found. Generate one with:")
        print("       ssh-keygen -t ed25519 -f ssh_host_ed25519_key -N \"\"")
        print("       ssh-keygen -t rsa -b 4096 -f ssh_host_rsa_key -N \"\"")
        return
    print(f"[ssh] host keys loaded: {', '.join(name for name, _ in host_keys)}")
    print(f"BBS Selector (SSH) listening on {LISTEN_HOST}:{LISTEN_PORT}")
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((LISTEN_HOST, LISTEN_PORT))
    server_sock.listen(10)

    try:
        while True:
            client_sock, client_addr = server_sock.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr, host_keys),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()
