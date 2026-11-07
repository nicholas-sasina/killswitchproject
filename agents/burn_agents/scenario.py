"""Read this agent's part of the scenario file.

The scenario YAML is the only source of instructions, tools and the agent
graph. The code decides how an agent behaves from its *tools*, not its id:
an agent that can call list_tickets works the ticket queue, any other agent
works its inbox.
"""
import yaml


def load_scenario(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def agent_spec(scenario: dict, agent_id: str) -> dict:
    for agent in scenario["agents"]:
        if agent["id"] == agent_id:
            return agent
    known = ", ".join(a["id"] for a in scenario["agents"])
    raise SystemExit(f"agent {agent_id} is not in scenario {scenario['name']} (known: {known})")


def works_ticket_queue(spec: dict) -> bool:
    return "list_tickets" in spec["tools"]
