"""Ark replaces only model I/O; business decisions stay in existing services."""

import json
from types import SimpleNamespace

from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.agent.contracts import ExplanationFact
from app.integrations.agent.explanation import arrange_facts
from app.integrations.agent.client import ArkExplanationClient
from app.integrations.dispatch_agent.contracts import DispatchContext
from app.integrations.dispatch_agent.planner import ArkIntentPlanner


def completion(name, arguments):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        tool_calls=[SimpleNamespace(function=SimpleNamespace(
            name=name, arguments=json.dumps(arguments),
        ))],
    ))])


class FakeArk:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_ark_explanation_uses_thinking_and_only_reorders_verified_facts():
    ark = FakeArk(completion("arrange_explanation", {"fact_ids": ["second", "first"]}))
    facts = (ExplanationFact(id="first", text="fact one"),
             ExplanationFact(id="second", text="fact two"))

    arranged, source, diagnostics = arrange_facts(
        facts, ArkExplanationClient("deepseek-v4-flash-ga-260731", "test-key", client=ark),
    )

    assert tuple(f.id for f in arranged) == ("second", "first")
    assert (source, diagnostics) == ("model", ())
    request = ark.calls[0]
    assert request["model"] == "deepseek-v4-flash-ga-260731"
    assert request["thinking"] == {"type": "enabled"}
    assert request["tool_choice"] == {"type": "function", "function": {"name": "arrange_explanation"}}
    assert request["messages"][1]["role"] == "user"
    assert "fact one" in request["messages"][1]["content"]


def test_ark_explanation_malformed_output_uses_existing_template_fallback():
    ark = FakeArk(completion("arrange_explanation", {"fact_ids": ["invented"]}))
    facts = (ExplanationFact(id="trusted", text="verified"),)

    arranged, source, diagnostics = arrange_facts(
        facts, ArkExplanationClient("model", "test-key", client=ark),
    )

    assert arranged == facts
    assert (source, diagnostics) == ("fallback", ("AGENT_EXPLANATION_FALLBACK",))


def test_ark_intent_uses_thinking_and_existing_action_whitelist():
    ark = FakeArk(completion("plan_dispatch", {"actions": ["get_delivery_status"]}))

    result = ArkIntentPlanner("deepseek-v4-flash-ga-260731", "test-key", client=ark).plan(
        "今天运营如何", DispatchContext(),
    )

    assert result.actions == ("get_delivery_status",)
    request = ark.calls[0]
    assert request["thinking"] == {"type": "enabled"}
    assert request["tool_choice"] == {"type": "function", "function": {"name": "plan_dispatch"}}
    assert "今天运营如何" in request["messages"][1]["content"]


def test_ark_clients_take_key_and_long_timeout_from_settings(monkeypatch):
    from app.api import dependencies
    from app.api.routes import dispatch

    settings = Settings(_env_file=None, ark_api_key=SecretStr("test-only-secret"),
                        dispatch_context_secret=SecretStr("s" * 32), ark_timeout_seconds=1800)
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(dispatch, "get_settings", lambda: settings)

    explanation = dependencies.get_recovery_explanation_client(mode="agent")
    service = dispatch.get_dispatch_service(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))

    assert isinstance(explanation, ArkExplanationClient)
    assert isinstance(service.planner, ArkIntentPlanner)
    assert isinstance(service.explanation_client, ArkExplanationClient)
    assert explanation.api_key == service.planner.api_key == "test-only-secret"
    assert explanation.timeout == service.planner.timeout == 1800


def test_ark_without_key_retains_rule_and_template_fallback(monkeypatch):
    from app.api import dependencies
    from app.api.routes import dispatch

    settings = Settings(_env_file=None, ark_api_key=None,
                        dispatch_context_secret=SecretStr("s" * 32))
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(dispatch, "get_settings", lambda: settings)

    assert dependencies.get_recovery_explanation_client(mode="agent") is None
    service = dispatch.get_dispatch_service(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    assert service.planner is None
    assert service.explanation_client is None


def test_real_ark_sdk_serializes_thinking_and_forced_tool_without_network():
    from httpx import Client, MockTransport, Response
    from volcenginesdkarkruntime import Ark

    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        return Response(200, json={
            "id": "local-test", "object": "chat.completion", "created": 1,
            "model": "deepseek-v4-flash-ga-260731",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "arrange_explanation", "arguments": '{"fact_ids":["trusted"]}',
                }}],
            }}],
        })

    transport = MockTransport(reply)
    with Client(transport=transport) as http_client:
        ark = Ark(api_key="test-only-key", timeout=1800, max_retries=0, http_client=http_client)
        result = ArkExplanationClient(
            "deepseek-v4-flash-ga-260731", "test-only-key", client=ark,
        ).arrange((ExplanationFact(id="trusted", text="verified"),))

    assert result.fact_ids == ("trusted",)
    assert requests[0]["thinking"] == {"type": "enabled"}
    assert requests[0]["tool_choice"]["function"]["name"] == "arrange_explanation"
