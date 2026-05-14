from packages.llm.gemini import _to_gemini_messages, _to_gemini_schema


def test_to_gemini_schema_passes_through_parameters():
    tools = [
        {
            "name": "compute_metric",
            "description": "Computes a metric.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_id": {"type": "string"},
                },
                "required": ["metric_id"],
            },
        }
    ]
    out = _to_gemini_schema(tools)
    assert out[0]["name"] == "compute_metric"
    assert out[0]["parameters"]["properties"]["metric_id"]["type"] == "string"


def test_to_gemini_messages_maps_user_and_model_roles():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi back"},
    ]
    out = _to_gemini_messages(msgs)
    assert out[0]["role"] == "user"
    assert out[0]["parts"][0]["text"] == "hello"
    assert out[1]["role"] == "model"


def test_to_gemini_messages_handles_tool_results():
    msgs = [
        {
            "role": "tool",
            "tool_name": "compute_metric",
            "content": {"value": 4823, "provenance": {"query_hash": "abc"}},
        },
    ]
    out = _to_gemini_messages(msgs)
    assert out[0]["role"] == "user"
    assert "function_response" in out[0]["parts"][0]
    fr = out[0]["parts"][0]["function_response"]
    assert fr["name"] == "compute_metric"
    assert fr["response"]["value"] == 4823


def test_empty_tool_list_produces_empty_translation():
    assert _to_gemini_schema([]) == []
