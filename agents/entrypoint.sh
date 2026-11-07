#!/bin/sh
# The proxy writes its CA certificate to the shared volume when it starts.
# Wait for it so the first HTTPS call is not rejected as untrusted.
i=0
while [ ! -s "${SSL_CERT_FILE:-/certs/mitmproxy-ca-cert.pem}" ] && [ $i -lt 60 ]; do
  i=$((i + 1)); sleep 1
done
exec python -m burn_agents
