#!/bin/sh
# Behind the proxy, wait for the CA certificate it writes to the shared volume, then run the command given.
if [ -n "$SSL_CERT_FILE" ]; then
  i=0
  while [ ! -s "$SSL_CERT_FILE" ] && [ $i -lt 60 ]; do
    i=$((i + 1)); sleep 1
  done
fi
exec "$@"
