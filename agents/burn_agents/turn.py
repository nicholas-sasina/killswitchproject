"""One model turn: build a Strands agent, give it a prompt, let it use tools."""
import logging
import threading
from time import monotonic

from strands import Agent, tool
from strands.hooks import (AfterToolCallEvent, BeforeModelCallEvent, BeforeToolCallEvent, HookProvider,
                           HookRegistry)
from strands.models import BedrockModel

from .cloud import check_cloud_access, check_provider
from .gateway import result_text
from .revoked import Revoked, is_refusal_text

log = logging.getLogger("burn_agents")


class RefusalWatch(HookProvider):
    """Notices when the gateway refuses a tool call and stops further tool and model calls.

    Only tool names and status are logged: tool output can hold credentials.
    """

    def __init__(self):
        self.refusal = ""

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)
        registry.add_callback(BeforeModelCallEvent, self._before_model)

    def _before(self, event: BeforeToolCallEvent) -> None:
        if self.refusal:
            event.cancel_tool = "BURN: access revoked, not calling any more tools"

    def _before_model(self, event: BeforeModelCallEvent) -> None:
        if self.refusal:
            event.cancel = "BURN: access revoked, not calling the model again"

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


class CallBudget:
    """A hard limit on model calls across turns (one worker's share of a run). Each call is counted before it is made.

    close() refuses every later call, so once it is closed `used` is final even while a turn is still running.
    """

    def __init__(self, limit: int):
        self.limit, self.used, self.closed = limit, 0, False
        self._lock = threading.Lock()  # turns run in a worker thread; close() comes from the main one

    @property
    def left(self) -> int:
        return 0 if self.closed else max(self.limit - self.used, 0)

    def take(self) -> bool:
        with self._lock:
            if self.closed or self.used >= self.limit:
                return False
            self.used += 1
            return True

    def close(self) -> None:
        with self._lock:
            self.closed = True


class TurnCut(Exception):
    """The turn was cut off: ModelCallCap refused a model call, so the model never finished."""


class ModelCallCap(HookProvider):
    """Cancels a model call past `limit` in one turn, one the `budget` refuses, or any call once the turn has run
    `seconds`, so a model that keeps calling tools cannot run up the bill or the clock. Strands then ends the turn as
    if the model had (stop reason "end_turn"), so `cut` records why, and TurnRunner raises TurnCut. A call already
    under way is bounded by the model's max_tokens and the client's read timeout.
    """

    def __init__(self, limit: int, budget: CallBudget | None = None, seconds: float | None = None):
        self.limit, self.budget, self.calls, self.cut, self.seconds = limit, budget, 0, "", seconds
        self._deadline = None if seconds is None else monotonic() + seconds

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeModelCallEvent, self._before)

    def _before(self, event: BeforeModelCallEvent) -> None:
        if event.cancel:  # already cancelled (a refusal): nothing to count
            return
        if self._deadline is not None and monotonic() >= self._deadline:
            self.cut = event.cancel = f"stopped at the turn's deadline of {self.seconds:g} s"
        elif self.calls >= self.limit:
            self.cut = event.cancel = f"stopped after {self.limit} model calls in one turn"
        elif self.budget is not None and not self.budget.take():
            self.cut = event.cancel = f"model-call budget of {self.budget.limit} spent"
        else:
            self.calls += 1


def make_model(model_id: str, session, client_config=None, max_tokens: int | None = None) -> BedrockModel:
    # Temperature 0 so a replay of the same run behaves the same way. client_config is a botocore Config
    # (retries, timeouts) for the Bedrock client; None keeps Strands' own. max_tokens caps each call's output;
    # None keeps Strands' default, which for Haiku 4.5 is its 64,000-token maximum.
    extra = {} if max_tokens is None else {"max_tokens": max_tokens}
    return BedrockModel(model_id=model_id, boto_session=session, temperature=0, boto_client_config=client_config,
                        **extra)


class TurnRunner:
    def __init__(self, model, system_prompt: str, tools: list, max_model_calls: int | None = None,
                 budget: CallBudget | None = None, turn_seconds: float | None = None):
        self._model, self._system_prompt, self._tools = model, system_prompt, tools
        self._max_model_calls, self._budget, self._turn_seconds = max_model_calls, budget, turn_seconds

    def __call__(self, prompt: str):
        """Run one turn and return its AgentResult. Raises Revoked when the gateway refused the badge, even if the
        turn then failed, and TurnCut when the turn was cut off at a cap or its deadline."""
        # A fresh Agent per turn keeps each ticket's context small and cheap.
        watch = RefusalWatch()
        capped = any(x is not None for x in (self._max_model_calls, self._budget, self._turn_seconds))
        cap = ModelCallCap(self._max_model_calls or 10**9, self._budget, self._turn_seconds) if capped else None
        agent = Agent(model=self._model, system_prompt=self._system_prompt, tools=self._tools,
                      hooks=[watch] + ([cap] if cap else []), callback_handler=None)
        try:
            result = agent(prompt)
        finally:
            if watch.refusal:
                raise Revoked(watch.refusal)  # a burn is never lost to a later error in the same turn
        if cap and cap.cut:
            raise TurnCut(cap.cut)
        return result
