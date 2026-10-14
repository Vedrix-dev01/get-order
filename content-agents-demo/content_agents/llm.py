"""Claude access for the agents.

`ClaudeLLM` calls the Claude API with schema-validated (structured) output, so
every agent hands the next one a typed object instead of free text.
`DemoLLM` (demo_llm.py) implements the same interface offline.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from .models import AgentUsage

T = TypeVar("T", bound=BaseModel)

# On a safety-classifier decline, the API re-runs the request on Anthropic's
# recommended fallback model server-side instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AgentError(RuntimeError):
    pass


class LLM(Protocol):
    mode: str

    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: type[T],
        context: dict[str, Any],
        usage: AgentUsage,
        effort: str = "medium",
    ) -> T: ...

    def web_research(self, *, system: str, prompt: str, usage: AgentUsage) -> str | None: ...


class ClaudeLLM:
    mode = "live"

    def __init__(self, model: str, client: Any | None = None) -> None:
        import anthropic

        self.model = model
        self.client = client or anthropic.Anthropic()

    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: type[T],
        context: dict[str, Any],
        usage: AgentUsage,
        effort: str = "medium",
    ) -> T:
        started = time.monotonic()
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        self._record(usage, response, started)
        if response.stop_reason == "refusal":
            raise AgentError(f"{agent}: request declined ({getattr(response.stop_details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise AgentError(f"{agent}: output hit max_tokens before the JSON was complete")
        if response.parsed_output is None:
            raise AgentError(f"{agent}: no structured output returned")
        return response.parsed_output

    def web_research(self, *, system: str, prompt: str, usage: AgentUsage) -> str | None:
        """Let Claude search the web for current trends; returns a text brief."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        started = time.monotonic()
        response = None
        for _ in range(4):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=messages,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
                output_config={"effort": "medium"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            if response.stop_reason != "pause_turn":
                break
            # Long server-tool turns pause; send the partial turn back to resume it.
            messages.append({"role": "assistant", "content": response.content})
        self._record(usage, response, started)
        if response is None or response.stop_reason == "refusal":
            return None
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        return text or None

    @staticmethod
    def _record(usage: AgentUsage, response: Any, started: float) -> None:
        usage.calls += 1
        usage.seconds += time.monotonic() - started
        if response is not None and response.usage is not None:
            usage.input_tokens += response.usage.input_tokens or 0
            usage.output_tokens += response.usage.output_tokens or 0


def make_llm(settings) -> LLM:
    if settings.llm_mode == "live":
        return ClaudeLLM(settings.model)
    from .demo_llm import DemoLLM

    return DemoLLM(latency_s=settings.demo_latency_s)
