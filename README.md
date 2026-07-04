<img width="1178" height="844" alt="image" src="https://github.com/user-attachments/assets/f6ca797e-6344-4f33-a1ae-99d6206c8a2e" />


# ANetBBS Selector

ANetBBS Selector is a small Python-based BBS connection selector.
Callers connect to one public Telnet, SSH, or Rlogin port, see an ANSI menu,
choose a destination BBS, and are relayed to the selected system.

This package includes:

| File | Purpose |
| --- | --- |
| `bbs_selector.py` | Master launcher that starts and watches the Telnet, SSH, and Rlogin selectors. |
| `bbs_selector_telnet.py` | Telnet selector/listener. Default listen port: `1337`. |
| `bbs_selector_ssh.py` | SSH selector/listener. Default listen port: `1338`. |
| `bbs_selector_rlogin.py` | Rlogin selector/listener. Default listen port: `1339`. |
| `bbs-selectors.service` | Example systemd service. |
| `file_id.diz` | BBS-style package description. |

## What it does

Each selector script has its own `BBS_LIST` near the top of the file. Edit those lists to match your systems.

Example from the Telnet selector:

```python
BBS_LIST = [
    ("My Main BBS", "bbs.example.com", 23),
    ("My Test BBS", "test.example.com", 2323),
]
```

The selector automatically treats the next menu number after your BBS list as the quit/exit option.

## Requirements

Tested for Linux systems running Python 3.

Install the required packages:

### Debian / Ubuntu / Raspberry Pi OS

```bash
sudo apt update
sudo apt install -y python3 python3-pip openssh-client openssh-server
python3 -m pip install --user paramiko
```

If your distro blocks user `pip` installs, use a virtual environment instead:

```bash
sudo apt install -y python3-venv
python3 -m venv ~/bbs-selector/venv
~/bbs-selector/venv/bin/pip install paramiko
```

The Telnet and Rlogin selectors use only Python's standard library. The SSH selector requires `paramiko`.

## Install

These instructions assume you are installing to `/opt/bbs-selector` and running the service as a dedicated `bbsselector` user.

```bash
sudo useradd --system --home /opt/bbs-selector --shell /usr/sbin/nologin bbsselector
sudo mkdir -p /opt/bbs-selector
sudo cp bbs_selector*.py /opt/bbs-selector/
sudo cp *.ans /opt/bbs-selector/ 2>/dev/null || true
sudo chown -R bbsselector:bbsselector /opt/bbs-selector
```

If you are using a Python virtual environment:

```bash
sudo python3 -m venv /opt/bbs-selector/venv
sudo /opt/bbs-selector/venv/bin/pip install paramiko
sudo chown -R bbsselector:bbsselector /opt/bbs-selector
```

## ANSI welcome screens

The scripts look for these ANSI files in the same directory as the Python scripts:

| Protocol | ANSI file |
| --- | --- |
| Telnet | `welcome.ans` |
| SSH | `welcomessh.ans` |
| Rlogin | `welcomerlog.ans` |

They are optional. If a file is missing, the selector shows a plain fallback welcome message. For a public package, include sample ANSI screens or tell users to create their own.

## SSH host keys

The SSH selector needs at least one host key file in the same directory as `bbs_selector_ssh.py`.

Generate recommended keys:

```bash
cd /opt/bbs-selector
sudo -u bbsselector ssh-keygen -t ed25519 -f ssh_host_ed25519_key -N ""
sudo -u bbsselector ssh-keygen -t rsa -b 4096 -f ssh_host_rsa_key -N ""
```

The SSH selector looks for these filenames:

```text
ssh_host_ed25519_key
ssh_host_ecdsa_key
ssh_host_rsa_key
```

## Configure your BBS list

Edit each protocol script and update `BBS_LIST`:

```bash
sudo nano /opt/bbs-selector/bbs_selector_telnet.py
sudo nano /opt/bbs-selector/bbs_selector_ssh.py
sudo nano /opt/bbs-selector/bbs_selector_rlogin.py
```

Also check the listen ports near the top of each file:

```python
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 1337
```

Default public ports:

| Protocol | Default listen port |
| --- | --- |
| Telnet | `1337` |
| SSH | `1338` |
| Rlogin | `1339` |

Open or forward those ports on your firewall/router as needed.

## Test manually

Before installing the service, test each selector from the install directory.

```bash
cd /opt/bbs-selector
python3 bbs_selector_telnet.py
```

From another terminal:

```bash
telnet your-server-ip 1337
```

For SSH:

```bash
cd /opt/bbs-selector
python3 bbs_selector_ssh.py
```

Then connect with:

```bash
ssh -p 1338 username@your-server-ip
```

For Rlogin, use SyncTERM or another BBS client that supports Rlogin:

```text
rlogin://your-server-ip:1339
```

## Install the systemd service

Edit the included service file first:

```bash
sudo nano bbs-selectors.service
```

Recommended service file:

```ini
[Unit]
Description=ANetBBS Selector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bbsselector
Group=bbsselector
WorkingDirectory=/opt/bbs-selector
ExecStart=/usr/bin/python3 /opt/bbs-selector/bbs_selector.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

If you installed Paramiko inside `/opt/bbs-selector/venv`, use this `ExecStart` instead:

```ini
ExecStart=/opt/bbs-selector/venv/bin/python /opt/bbs-selector/bbs_selector.py
```

Copy, enable, and start the service:

```bash
sudo cp bbs-selectors.service /etc/systemd/system/bbs-selectors.service
sudo systemctl daemon-reload
sudo systemctl enable --now bbs-selectors.service
```

Check status and logs:

```bash
systemctl status bbs-selectors.service
journalctl -u bbs-selectors.service -f
```

Restart after edits:

```bash
sudo systemctl restart bbs-selectors.service
```

Stop it:

```bash
sudo systemctl stop bbs-selectors.service
```

## Notes for GitHub packaging

Recommended repository layout:

```text
bbs-selector/
├── README.md
├── file_id.diz
├── bbs-selectors.service
├── bbs_selector.py
├── bbs_selector_telnet.py
├── bbs_selector_ssh.py
├── bbs_selector_rlogin.py
├── welcome.ans
├── welcomessh.ans
└── welcomerlog.ans
```

Do not commit private SSH host keys. Users should generate their own.

Recommended `.gitignore` entries:

```gitignore
ssh_host_*_key
ssh_host_*_key.pub
paramiko_debug.log
__pycache__/
*.pyc
venv/
```

## Troubleshooting

### SSH selector exits immediately

Generate SSH host keys in the selector directory:

```bash
sudo -u bbsselector ssh-keygen -t ed25519 -f /opt/bbs-selector/ssh_host_ed25519_key -N ""
```

### SSH clients cannot connect

Make sure Paramiko is installed for the same Python interpreter used by systemd. Check logs:

```bash
journalctl -u bbs-selectors.service -n 100 --no-pager
```

### Welcome ANSI does not display

Make sure the ANSI file exists in the selector directory and has the exact expected filename:

```text
welcome.ans
welcomessh.ans
welcomerlog.ans
```

### Port already in use

Check what is using the port:

```bash
sudo ss -ltnp | grep -E ':1337|:1338|:1339'
```

Either stop the conflicting service or change `LISTEN_PORT` in the selector script.

## License

https://github.com/anetonline/ANetBBS-Selector?tab=GPL-3.0-1-ov-file
