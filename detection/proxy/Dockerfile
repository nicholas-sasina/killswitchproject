FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'boto3>=1.35' 'mitmproxy>=11,<13'
COPY contracts /app/contracts
COPY detection /app/detection
RUN pip install --no-cache-dir --no-deps /app/contracts /app/detection
ENV PYTHONUNBUFFERED=1 AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
EXPOSE 8080
VOLUME /certs
ENTRYPOINT ["mitmdump", "-q", "-s", "/app/detection/proxy/addon.py", "--listen-host", "0.0.0.0", "--listen-port", "8080", "--set", "confdir=/certs", "--set", "connection_strategy=lazy", "--set", "upstream_cert=false", "--set", "flow_detail=0", "--set", "termlog_verbosity=error", "--set", "block_global=false", "--set", "body_size_limit=256k", "--set", "websocket=false", "--set", "rawtcp=false", "--set", "http2=false"]
