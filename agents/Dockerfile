# One image for every agent; AGENT_ID and the scenario file pick the behaviour.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY burn_agents ./burn_agents
RUN pip install --no-cache-dir .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
