#!/usr/bin/env bash
# Install one restricted SSH backup reader; never modify existing accounts or keys.
set -euo pipefail

ACCOUNT="km-backup"
ACCOUNT_HOME="/var/lib/knowledge-manager-backup"
INSTALL_DIR="/usr/local/libexec/knowledge-manager-backup"
SUDOERS="/etc/sudoers.d/knowledge-manager-backup"
BACKUP_ROOT="/var/backups/knowledge-manager"

fail() { printf '%s\n' "$*" >&2; exit 1; }
if [[ $# -ne 2 || "${1:-}" != "--install" ]]; then
    fail "Usage: sudo bash $0 --install PUBLIC_KEY_FILE"
fi
[[ ${EUID} -eq 0 ]] || fail "Run as root on Ubuntu 24.04."
[[ -r /etc/os-release ]] || fail "Cannot verify the operating system."
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] \
    || fail "Only Ubuntu 24.04 is supported."
for command in getent groupadd useradd usermod install stat readlink ssh-keygen visudo; do
    command -v "${command}" >/dev/null || fail "Required command unavailable: ${command}"
done
[[ -x /usr/bin/python3 && -x /usr/bin/sudo ]] || fail "Python 3 and sudo are required."

trusted_directory() {
    local directory="$1" mode
    [[ -d "${directory}" && ! -L "${directory}" ]] \
        || fail "Expected a real directory: ${directory}"
    [[ "$(readlink -f -- "${directory}")" == "${directory}" ]] \
        || fail "Refusing a linked ancestor: ${directory}"
    [[ "$(stat -c '%u' -- "${directory}")" == 0 ]] \
        || fail "Directory must be root-owned: ${directory}"
    mode="$(stat -c '%a' -- "${directory}")"
    (( (8#${mode} & 022) == 0 )) || fail "Directory must not be group/world-writable: ${directory}"
}

trusted_source() {
    local file="$1" directory mode
    [[ "${file}" == /* && -f "${file}" && ! -L "${file}" ]] \
        || fail "Source must be an absolute regular file: ${file}"
    [[ "$(readlink -f -- "${file}")" == "${file}" ]] \
        || fail "Source has a linked ancestor: ${file}"
    [[ "$(stat -c '%u' -- "${file}")" == 0 ]] \
        || fail "Stage sources and public key in a root-owned directory first."
    mode="$(stat -c '%a' -- "${file}")"
    (( (8#${mode} & 022) == 0 )) || fail "Source must not be group/world-writable."
    directory="$(dirname -- "${file}")"
    while :; do
        trusted_directory "${directory}"
        [[ "${directory}" != / ]] || break
        directory="$(dirname -- "${directory}")"
    done
}

# Every conflict check precedes account creation or changes under system paths.
if getent passwd "${ACCOUNT}" >/dev/null || getent group "${ACCOUNT}" >/dev/null; then
    fail "Account/group ${ACCOUNT} already exists; inspect manually, do not overwrite."
fi
for path in "${ACCOUNT_HOME}" "${INSTALL_DIR}" "${SUDOERS}"; do
    [[ ! -e "${path}" && ! -L "${path}" ]] || fail "Conflicting existing path: ${path}"
done
for path in /var/lib /usr/local /etc/sudoers.d "${BACKUP_ROOT}"; do
    trusted_directory "${path}"
done
if [[ -e /usr/local/libexec || -L /usr/local/libexec ]]; then
    trusted_directory /usr/local/libexec
fi

SOURCE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/export_backup.py"
PUBLIC_KEY="$2"
[[ -f "${SOURCE}" && ! -L "${SOURCE}" ]] || fail "Exporter source missing or linked."
[[ -f "${PUBLIC_KEY}" && ! -L "${PUBLIC_KEY}" ]] || fail "Public key must be a regular file."
trusted_source "${SOURCE}"
trusted_source "${PUBLIC_KEY}"
[[ "$(stat -c '%s' -- "${PUBLIC_KEY}")" -le 4096 ]] || fail "Public key file is too large."
mapfile -t public_lines < "${PUBLIC_KEY}"
[[ ${#public_lines[@]} -eq 1 ]] || fail "Provide exactly one ssh-ed25519 public-key line."
public_line="${public_lines[0]%$'\r'}"
if [[ "${public_line}" =~ ^ssh-ed25519[[:blank:]]+([A-Za-z0-9+/]+={0,2})([[:blank:]].*)?$ ]]; then
    clean_key="ssh-ed25519 ${BASH_REMATCH[1]}"
else
    fail "Only an ordinary ssh-ed25519 public key is accepted; options are forbidden."
fi

umask 077
staging="$(mktemp -d /tmp/knowledge-manager-backup-install.XXXXXX)"
mutated=0
cleanup() {
    local result=$?
    rm -f -- "${staging}/key.pub" "${staging}/export_backup.py" \
        "${staging}/request" "${staging}/sudoers" "${staging}/authorized_keys"
    rmdir -- "${staging}" || true
    if [[ ${result} -ne 0 && ${mutated} -eq 1 ]]; then
        printf '%s\n' 'Installation stopped after system changes. Inspect the dedicated account and paths manually; no recursive cleanup or overwrite was attempted.' >&2
    fi
    exit "${result}"
}
trap cleanup EXIT
printf '%s\n' "${clean_key}" > "${staging}/key.pub"
ssh-keygen -l -f "${staging}/key.pub" >/dev/null \
    || fail "ssh-keygen rejected the supplied public key."
install -m 0600 -- "${SOURCE}" "${staging}/export_backup.py"
/usr/bin/python3 -I -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' \
    "${staging}/export_backup.py"

cat > "${staging}/request" <<'WRAPPER'
#!/bin/sh
set -eu
exec /usr/bin/sudo -n -- /usr/bin/python3 -I /usr/local/libexec/knowledge-manager-backup/export_backup.py "${SSH_ORIGINAL_COMMAND-}"
WRAPPER
printf '%s\n' 'km-backup ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/libexec/knowledge-manager-backup/export_backup.py *' \
    > "${staging}/sudoers"
visudo -cf "${staging}/sudoers" >/dev/null || fail "Invalid dedicated sudoers rule."
printf 'restrict,command="%s/request" %s\n' "${INSTALL_DIR}" "${clean_key}" \
    > "${staging}/authorized_keys"

mutated=1
groupadd --system "${ACCOUNT}"
useradd --system --gid "${ACCOUNT}" --home-dir "${ACCOUNT_HOME}" \
    --no-create-home --shell /bin/sh "${ACCOUNT}"
usermod --lock "${ACCOUNT}"
if [[ ! -d /usr/local/libexec ]]; then
    install -d -o root -g root -m 0755 /usr/local/libexec
fi
mkdir -- "${INSTALL_DIR}" "${ACCOUNT_HOME}"
chown root:root "${INSTALL_DIR}"
chmod 0755 "${INSTALL_DIR}"
chown "root:${ACCOUNT}" "${ACCOUNT_HOME}"
chmod 0750 "${ACCOUNT_HOME}"
install -d -o root -g "${ACCOUNT}" -m 0750 "${ACCOUNT_HOME}/.ssh"
install -o root -g root -m 0644 "${staging}/export_backup.py" "${INSTALL_DIR}/export_backup.py"
install -o root -g root -m 0755 "${staging}/request" "${INSTALL_DIR}/request"
install -o root -g "${ACCOUNT}" -m 0640 "${staging}/authorized_keys" "${ACCOUNT_HOME}/.ssh/authorized_keys"
install -o root -g root -m 0440 "${staging}/sudoers" "${SUDOERS}"
visudo -cf "${SUDOERS}" >/dev/null
printf '%s\n' 'Installed restricted km-backup reader. Existing SSH configuration, ports, firewall and keys were not changed.'
printf '%s\n' 'Verify latest/get over the dedicated key and denied shell/forwarding before scheduling copies.'
