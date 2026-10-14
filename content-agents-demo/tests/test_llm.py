from types import SimpleNamespace

import pytest

from content_agents.llm import FALLBACK_BETA, AgentError, ClaudeLLM
from content_agents.models import AgentUsage, ResearchOutput


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def response(stop_reason="end_turn", parsed=None):
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, stop_details=None,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50))


def test_structured_call_shape_and_usage():
    parsed = ResearchOutput(angles=[])
    client, messages = fake_client(response(parsed=parsed))
    usage = AgentUsage()
    llm = ClaudeLLM("claude-opus-5-5", client=client)
    out = llm.structured(agent="researcher", system="sys", prompt="p", schema=ResearchOutput,
                         context={}, usage=usage, effort="high")
    assert out is parsed
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is ResearchOutput
    assert call["output_config"] == {"effort": "high"}
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert "thinking" not in call  # adaptive thinking is the model default
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 100, 50)


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_structured_raises_on_unusable_stop(stop):
    client, _ = fake_client(response(stop_reason=stop))
    with pytest.raises(AgentError):
        ClaudeLLM("m", client=client).structured(agent="a", system="s", prompt="p", schema=ResearchOutput,
                                                 context={}, usage=AgentUsage())
