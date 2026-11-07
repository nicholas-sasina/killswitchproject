"""One model turn: build a Strands agent, give it a prompt, let it use tools."""
import logging

from strands import Agent, tool
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry
from strands.models import BedrockModel

from .cloud import check_cloud_access, check_provider
from .gateway import result_text
from .revoked import Revoked, is_refusal_text

log = logging.getLogger("burn_agents")


class RefusalWatch(HookProvider):
    """Notices when the gateway refuses a tool call and stops further calls.

    Only tool names and status are logged: tool output can hold credentials.
    """

    def __init__(self):
        self.refusal = ""

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _before(self, event: BeforeToolCallEvent) -> None:
        if self.refusal:
            event.cancel_tool = "BURN: access revoked, not calling any more tools"

    def _after(self, event: AfterToolCallEvent) -> None:
        status = event.result.get("status")
        log.info("tool %s -> %s", event.tool_use["name"], status)
        if status == "error" and is_refusal_text(result_text(event.result)):
            self.refusal = result_text(event.result)


def local_tools(session, provider_url: str, api_key: str) -> list:
    @tool
    def check_cloud_access_tool() -> str:
        """Check which AWS identity you are running as and how many S3 buckets you can see."""
        return check_cloud_access(session)

    @tool
    def check_provider_tool() -> str:
        """Check that your provider API key is accepted by the provider."""
        return check_provider(provider_url, api_key)

    tools = [check_cloud_access_tool]
    return tools + [check_provider_tool] if provider_url else tools


def make_model(model_id: str, session) -> BedrockModel:
    # Temperature 0 so a replay of the same run behaves the same way.
    return BedrockModel(model_id=model_id, boto_session=session, temperature=0)


class TurnRunner:
    def __init__(self, model, system_prompt: str, tools: list):
        self._model, self._system_prompt, self._tools = model, system_prompt, tools

    def __call__(self, prompt: str) -> None:
        # A fresh Agent per turn keeps each ticket's context small and cheap.
        watch = RefusalWatch()
        agent = Agent(model=self._model, system_prompt=self._system_prompt, tools=self._tools,
                      hooks=[watch], callback_handler=None)
        agent(prompt)
        if watch.refusal:
            raise Revoked(watch.refusal)
