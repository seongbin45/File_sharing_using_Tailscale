# Console + tailscaled in one container.
#
# tailscaled runs in USERSPACE mode: a managed platform will not give a
# container the /dev/net/tun device a normal Tailscale node needs. In that
# mode there is no network interface - the tailnet is reachable only through
# the SOCKS5 proxy tailscaled opens, which is why the app is told about it
# with TSCONSOLE_SOCKS5 and dials every SSH connection through it.

FROM python:3.11-slim

# ca-certificates for the Tailscale control plane; curl to fetch the client.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Pinned, not "latest": an unattended redeploy should not silently change the
# thing that holds the network open.
ARG TAILSCALE_VERSION=1.78.1
RUN curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz" \
      -o /tmp/ts.tgz \
 && tar -xzf /tmp/ts.tgz -C /tmp \
 && mv /tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscale  /usr/local/bin/ \
 && mv /tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscaled /usr/local/sbin/ \
 && rm -rf /tmp/ts.tgz /tmp/tailscale_*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY hosts.example.json ./
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# tailscaled keeps its node key here. On a platform with no persistent disk
# this is lost on every deploy, so the container re-authenticates each time -
# use a reusable auth key, or attach a disk and mount it here.
VOLUME /var/lib/tailscale

ENV TSCONSOLE_BIND=0.0.0.0 \
    TSCONSOLE_BACKEND=ssh \
    TSCONSOLE_SOCKS5=localhost:1055 \
    PORT=8765

EXPOSE 8765
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
