FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'moto[server]>=5,<6' 'httpx>=0.27' 'boto3>=1.35'
COPY contracts /app/contracts
COPY detection /app/detection
RUN pip install --no-cache-dir --no-deps /app/contracts /app/detection
ENV PYTHONUNBUFFERED=1 AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
CMD ["python", "/app/detection/tests/integration/client.py"]
