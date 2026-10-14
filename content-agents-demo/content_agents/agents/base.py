from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..llm import LLM
from ..models import AgentUsage, RunState
from ..notify import Notifier
from ..store import Store


@dataclass
class AgentContext:
    settings: Settings
    store: Store
    llm: LLM
    notifier: Notifier


class Agent:
    name: str = "agent"
    label: str = "Agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def log(self, run: RunState, message: str, level: str = "info") -> None:
        self.ctx.store.log(run.id, self.name, message, level)

    def usage(self, run: RunState) -> AgentUsage:
        return run.usage.setdefault(self.name, AgentUsage())
