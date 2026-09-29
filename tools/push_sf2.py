#!/usr/bin/env python3
"""Upload .sf2/.sf3 files to the Pi and restart the player service.

Usage:
  SSHPASS='...' python3 tools/push_sf2.py foxy@192.168.1.197 a.sf2 b.sf2
  echo '...' | python3 tools/push_sf2.py foxy@192.168.1.197 a.sf2
Password source: SSHPASS env, else piped stdin, else interactive prompt.
Requires: pip install paramiko
"""
import getpass
import os
import posixpath
import sys

import paramiko


def get_password():
    if os.environ.get("SSHPASS"):
        return os.environ["SSHPASS"]
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data.splitlines()[0]
    return getpass.getpass("Pi SSH password: ")


def main():
    if len(sys.argv) < 3 or "@" not in sys.argv[1]:
        print(__doc__)
        sys.exit(2)
    user, host = sys.argv[1].split("@", 1)
    files = sys.argv[2:]
    for f in files:
        if not os.path.isfile(f):
            print(f"not a file: {f}")
            sys.exit(1)
    password = get_password()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"connecting to {user}@{host}...", flush=True)
    ssh.connect(host, username=user, password=password, timeout=15)
    try:
        sftp = ssh.open_sftp()
        try:
            sftp.mkdir("sfbox-incoming")
        except OSError:
            pass
        for f in files:
            dst = posixpath.join("sfbox-incoming", os.path.basename(f))
            print(f"upload {f} -> ~/{dst} "
                  f"({os.path.getsize(f)} bytes)", flush=True)
            sftp.put(f, dst)
        sftp.close()

        cmds = [
            "sudo -S mkdir -p /opt/minilab-sfbox/soundfonts",
            "sudo -S cp ~/sfbox-incoming/* /opt/minilab-sfbox/soundfonts/ "
            "&& rm -f ~/sfbox-incoming/*",
            "ls -la /opt/minilab-sfbox/soundfonts/",
        ]
        for c in cmds:
            print(f"$ {c}", flush=True)
            _, out, err = ssh.exec_command(c, timeout=60)
            out.channel.send(password + "\n")
            rc = out.channel.recv_exit_status()
            text = (out.read().decode() + err.read().decode()).strip()
            if text:
                print(text)
            if rc != 0:
                print(f"command failed (rc={rc}), stopping")
                sys.exit(1)

        _, out, err = ssh.exec_command(
            "sudo -S systemctl restart minilab-sfbox",
            timeout=30)
        out.channel.send(password + "\n")
        rc = out.channel.recv_exit_status()
        rest = (out.read().decode() + err.read().decode()).strip()
        print(f"service restart rc={rc} {rest}")
    finally:
        ssh.close()
    print("done.")


if __name__ == "__main__":
    main()
