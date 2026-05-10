import pytest

from packages.llm.client import LLMClient, LLMResponse, ToolCall
from packages.llm.fake import FakeLLMClient


def test_llm_response_default_empty_tool_calls():
    r = LLMResponse(text="hello")
    assert r.tool_calls == []
    assert r.text == "hello"


def test_tool_call_immutable():
    t = ToolCall(name="compute_metric", arguments={"metric_id": "gmv"})
    assert t.name == "compute_metric"
    # frozen dataclass — assignment raises FrozenInstanceError (AttributeError subclass)
    with pytest.raises(AttributeError):
        t.name = "other"  # type: ignore[misc]


def test_fake_client_implements_protocol():
    fake = FakeLLMClient([LLMResponse(text="ok")])
    assert isinstance(fake, LLMClient)


@pytest.mark.asyncio
async def test_fake_returns_scripted_responses_in_order():
    scripted = [
        LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})]),
        LLMResponse(text="answer is {{m:gmv_0}}"),
    ]
    fake = FakeLLMClient(scripted)
    r1 = await fake.generate("sys", [{"role": "user", "content": "hi"}], [])
    r2 = await fake.generate("sys", [{"role": "user", "content": "hi"}], [])
    assert r1.tool_calls[0].name == "compute_metric"
    assert r2.text == "answer is {{m:gmv_0}}"


@pytest.mark.asyncio
async def test_fake_records_calls_for_test_assertions():
    fake = FakeLLMClient([LLMResponse(text="ok")])
    await fake.generate("sys-x", [{"role": "user", "content": "Q"}], [{"name": "t"}])
    assert len(fake.calls) == 1
    system, messages, tools, model = fake.calls[0]
    assert system == "sys-x"
    assert tools[0]["name"] == "t"


@pytest.mark.asyncio
async def test_fake_raises_when_script_exhausted():
    fake = FakeLLMClient([LLMResponse(text="one")])
    await fake.generate("s", [], [])
    with pytest.raises(RuntimeError, match="no more scripted"):
        await fake.generate("s", [], [])
