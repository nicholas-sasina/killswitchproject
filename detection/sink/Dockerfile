FROM python:3.12-slim
WORKDIR /app
COPY detection/sink /app/sink
ENV PYTHONUNBUFFERED=1
EXPOSE 8081
VOLUME /data
CMD ["python", "-m", "sink"]
