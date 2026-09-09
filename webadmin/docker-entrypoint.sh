#!/bin/sh
# Bring up tailscaled, then the console.
#
# Everything here fails loudly and early. A container that starts without a
# tailnet serves a console that cannot reach anything, and a container that
# starts without a password serves an open shell - both are worse than a
# crash loop with a clear reason in the log.

set -eu

if [ -z "${TSCONSOLE_PASSWORD:-}" ]; then
    echo "FATAL: TSCONSOLE_PASSWORD is not set." >&2
    echo "  This console opens an interactive shell on other machines." >&2
    echo "  Refusing to start on a public bind address without a password." >&2
    exit 1
fi

if [ -z "${TS_AUTHKEY:-}" ]; then
    echo "FATAL: TS_AUTHKEY is not set - the container cannot join the tailnet." >&2
    echo "  Create one at https://login.tailscale.com/admin/settings/keys" >&2
    echo "  (reusable, and tag it so you can revoke this node on its own)." >&2
    exit 1
fi

echo "starting tailscaled (userspace networking, SOCKS5 on 1055)"
/usr/local/sbin/tailscaled \
    --state=/var/lib/tailscale/tailscaled.state \
    --socket=/var/run/tailscale/tailscaled.sock \
    --tun=userspace-networking \
    --socks5-server=localhost:1055 \
    --outbound-http-proxy-listen=localhost:1055 &
TAILSCALED_PID=$!

# --hostname makes this node identifiable in the admin console, which matters
# when the whole point of the key is being able to revoke exactly this one.
# --accept-dns=false: the container resolves nothing through the tailnet, the
# SOCKS5 proxy resolves MagicDNS names on our behalf (rdns=True in the app).
echo "authenticating to the tailnet"
until /usr/local/bin/tailscale --socket=/var/run/tailscale/tailscaled.sock up \
        --authkey="${TS_AUTHKEY}" \
        --hostname="${TS_HOSTNAME:-ts-control}" \
        --accept-dns=false; do
    if ! kill -0 "$TAILSCALED_PID" 2>/dev/null; then
        echo "FATAL: tailscaled exited before authentication completed." >&2
        exit 1
    fi
    echo "  retrying in 2s..."
    sleep 2
done

/usr/local/bin/tailscale --socket=/var/run/tailscale/tailscaled.sock status || true

echo "starting the console on ${TSCONSOLE_BIND:-0.0.0.0}:${PORT:-8765}"
exec python -m app.main
