"""Container entry point: one agent, chosen by AGENT_ID, same image for all."""
import logging
import os
import sys
import time

from .badge import BadgeToken
from .cloud import aws_session, check_cloud_access, check_provider, retry_while_key_activates
from .config import load_config
from .gateway import GatewayTools, open_client
from .loop import Worker, run_agent
from .scenario import agent_spec, load_scenario, works_ticket_queue
from .turn import TurnRunner, local_tools, make_model

log = logging.getLogger("burn_agents")


def step_gate(step_file: str):
    """Presenter mode: wait until the step file appears, consume it, go on."""
    if not step_file:
        return lambda: None

    def wait() -> None:
        log.info("waiting for the presenter to advance")
        while not os.path.exists(step_file):
            time.sleep(0.5)
        os.remove(step_file)

    return wait


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines are noise
    cfg = load_config()
    spec = agent_spec(load_scenario(cfg.scenario_file), cfg.agent_id)
    session = aws_session(cfg.aws_access_key_id, cfg.aws_secret_access_key)
    badge = BadgeToken(cfg.token_url, cfg.client_id, cfg.client_secret, cfg.scope)

    def heartbeat() -> None:
        # Activity under the agent's own keys; CloudTrail records it for the blast radius.
        log.info("cloud access: %s", check_cloud_access(session))
        if cfg.provider_url:
            log.info("provider: %s", check_provider(cfg.provider_url, cfg.api_key))

    def serve() -> None:
        retry_while_key_activates(heartbeat)
        client = open_client(cfg.gateway_url, badge)
        with client:
            allowed = set(spec["tools"])
            tools = [t for t in client.list_tools_sync() if t.tool_name in allowed]
            tools += local_tools(session, cfg.provider_url, cfg.api_key)
            runner = TurnRunner(make_model(cfg.model_id, session), spec["instructions"], tools)
            worker = Worker(GatewayTools(client).call, runner, heartbeat, pause=cfg.ticket_pause,
                            poll=cfg.poll_seconds, heartbeat_every=cfg.heartbeat_seconds,
                            repeat=cfg.repeat_queue, gate=step_gate(cfg.step_file))
            log.info("%s (%s) started", cfg.agent_id, spec["role"])
            worker.work_tickets() if works_ticket_queue(spec) else worker.work_inbox()

    run_agent(serve)
    return 0


if __name__ == "__main__":
    sys.exit(main())
