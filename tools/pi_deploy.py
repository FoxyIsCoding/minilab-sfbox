#!/usr/bin/env python3
"""Pull the newest commit on the Pi and restart the service (paramiko only)."""
import sys
import paramiko

HOST, USER, PW = "192.168.1.197", "foxy", "Micinka2"
CMDS = [
    ("cd /opt/minilab-sfbox && git fetch -q origin && "
     "git pull -q --ff-only && git log --oneline -1", False),
    ("sudo systemctl restart minilab-sfbox", True),
    ("sleep 6; systemctl is-active minilab-sfbox; "
     "systemctl show minilab-sfbox -p NRestarts --value; "
     "journalctl -u minilab-sfbox -n 14 --no-pager", False),
]


def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PW, timeout=15)
    for cmd, needs_pw in CMDS:
        print(f"$ {cmd}")
        _, out, err = ssh.exec_command(cmd, get_pty=needs_pw)
        if needs_pw:
            out.channel.send(PW + "\n")
        for line in out:
            print("  " + line.rstrip())
        for line in err:
            print("  ! " + line.rstrip())
    ssh.close()


if __name__ == "__main__":
    main()
