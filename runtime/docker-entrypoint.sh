#!/usr/bin/env bash
set -euo pipefail

sdk=/data/piper/sdk
ssh_dir=/data/piper/private/ssh
mkdir -p "$sdk" "$ssh_dir" /run/sshd
chmod 700 /data/piper/private "$ssh_dir"
for component in turn nvenc; do
    # Refuse to replace an existing real SDK directory from a bare-metal deployment.
    ln -sfnT "/opt/piper-sdk/$component" "$sdk/$component"
done
if [[ ! -f "$ssh_dir/ssh_host_ed25519_key" ]]; then
    ssh-keygen -q -t ed25519 -N '' -f "$ssh_dir/ssh_host_ed25519_key"
fi
chmod 600 "$ssh_dir/ssh_host_ed25519_key"
if [[ -f "$ssh_dir/authorized_keys" ]]; then
    chown root:root "$ssh_dir/authorized_keys"
    chmod 600 "$ssh_dir/authorized_keys"
fi
cat > /run/piper-sshd.conf <<'EOF'
Port 2222
ListenAddress 0.0.0.0
HostKey /data/piper/private/ssh/ssh_host_ed25519_key
AuthorizedKeysFile /data/piper/private/ssh/authorized_keys
PermitRootLogin prohibit-password
AllowUsers root
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
UsePAM yes
StrictModes yes
Subsystem sftp internal-sftp
EOF
/usr/sbin/sshd -t -f /run/piper-sshd.conf
exec /ros_entrypoint.sh "$@"
