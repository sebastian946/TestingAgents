"""The Designer with the REAL Anthropic SDK, mocking only the HTTP transport (WTA-23).

Where to mock an LLM is a design choice; this suite uses three levels, each for a reason:

1. HTTP transport (this file): `httpx2.MockTransport` answers in place of api.anthropic.com.
   The real SDK builds the request, parses the response and runs its own retries, so
   these tests catch breakage in how we use the SDK (wrong parameters, an SDK upgrade
   that changes behavior) and prove the SDK really retries 529s, which WTA-16 relies on.
2. SDK method (test_designer.py): a fake `client.beta.messages.create`. Fast and precise
   for our own logic: validation, self-healing retry, error mapping.
3. Our own wrapper (`fake_designer` in conftest.py): replaces `design_pages` for graph and
   worker tests, which care about the pipeline and not about the LLM at all.

No test here reaches the network: every request goes to `ScriptedAnthropic.handler`.
"""
import json
from unittest.mock import Mock

import anthropic
import httpx2
import pytest

from agents import designer
from agents.designer import DesignerError, DesignerFatalError, design_pages, design_scenarios
from models.agent_models import DesignerOutput, TestScenario

VALID_SCENARIOS = DesignerOutput(scenarios=[
    TestScenario(title="Login succeeds with valid credentials", steps=["Open the page", "Type <valid user>", "Click Login"],
                 expected_result="The products page opens", priority="critical", category="functional"),
    TestScenario(title="Login fails with a wrong password", steps=["Type a wrong password", "Click Login"],
                 expected_result="An error message is shown", priority="high", category="negative"),
]).model_dump_json()


def message(text: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5") -> httpx2.Response:
    """A Messages API response body, as api.anthropic.com returns it."""
    return httpx2.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 240, "output_tokens": 900,
                  "cache_creation_input_tokens": 2686, "cache_read_input_tokens": 0},
    })


def api_error(status: int, kind: str) -> httpx2.Response:
    # retry-after-ms keeps the SDK's backoff to 1 ms, so retry tests stay fast
    return httpx2.Response(status, headers={"retry-after-ms": "1"},
                           json={"type": "error", "error": {"type": kind, "message": kind}})


class ScriptedAnthropic:
    """A real anthropic.Anthropic client whose HTTP requests get scripted answers."""

    def __init__(self, *responses: httpx2.Response, max_retries: int = 2):
        self._responses = list(responses)
        self.handler = Mock(side_effect=self._answer)  # unittest.mock: counts and records calls
        transport = httpx2.MockTransport(self.handler)
        self.client = anthropic.Anthropic(
            api_key="sk-ant-test", base_url="https://api.anthropic.test", max_retries=max_retries,
            http_client=anthropic.DefaultHttpxClient(transport=transport),
        )

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

    @property
    def requests(self) -> list[httpx2.Request]:
        return [c.args[0] for c in self.handler.call_args_list]

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


# ------------------------------------------------------------- valid response

def test_a_valid_response_is_parsed_into_scenarios(login_page):
    api = ScriptedAnthropic(message(VALID_SCENARIOS))

    result = design_scenarios(login_page, "A clothing store", client=api.client, model="claude-opus-5-5")

    assert [s.title for s in result.scenarios] == ["Login succeeds with valid credentials", "Login fails with a wrong password"]
    assert result.scenarios[0].steps == ["Open the page", "Type <valid user>", "Click Login"]
    assert result.attempts == 1 and api.handler.call_count == 1
    assert (result.input_tokens, result.output_tokens, result.cache_creation_input_tokens) == (240, 900, 2686)


def test_the_request_the_sdk_sends_is_the_one_we_designed(login_page):
    api = ScriptedAnthropic(message(VALID_SCENARIOS))
    design_scenarios(login_page, "A clothing store", client=api.client, model="claude-opus-5-5", language="es")

    request, body = api.requests[0], api.body()
    assert request.url.path == "/v1/messages"
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert body["model"] == "claude-opus-5-5"
    assert "temperature" not in body  # current models reject it
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == designer.settings.designer_effort
    assert body["fallbacks"] == "default"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    user = body["messages"][0]["content"]
    assert '"type":"password"' in user and "A clothing store" in user
    assert "<output_language>Spanish" in user


# --------------------------------------------------------- broken JSON -> retry

def test_broken_json_triggers_exactly_one_retry_with_the_errors(login_page):
    api = ScriptedAnthropic(message('{"scenarios": [{"title": "Login'), message(VALID_SCENARIOS))

    result = design_scenarios(login_page, client=api.client)

    assert api.handler.call_count == 2  # the retry really happened, over HTTP
    assert result.attempts == 2 and len(result.scenarios) == 2
    retry = api.body(1)["messages"]
    assert len(retry) == 1  # a fresh single-turn request, no replayed assistant turn
    assert "<validation_errors>" in retry[0]["content"] and '{"scenarios": [{"title": "Login' in retry[0]["content"]


def test_two_broken_responses_give_up_on_the_page(login_page):
    api = ScriptedAnthropic(message("not json at all"))

    with pytest.raises(DesignerError, match="failed validation after 2 attempts") as info:
        design_scenarios(login_page, client=api.client)

    assert not isinstance(info.value, DesignerFatalError)
    assert api.handler.call_count == 2


# --------------------------------------------- API errors and the SDK's own retries

def test_the_sdk_retries_an_overloaded_api_and_the_page_succeeds(login_page):
    api = ScriptedAnthropic(api_error(529, "overloaded_error"), message(VALID_SCENARIOS), max_retries=2)

    result = design_scenarios(login_page, client=api.client)

    assert api.handler.call_count == 2  # 529, then success: retried by the SDK, not by us
    assert result.attempts == 1  # our self-healing retry was not involved


def test_a_persistent_529_becomes_a_page_error_after_the_sdk_gives_up(login_page):
    api = ScriptedAnthropic(api_error(529, "overloaded_error"), max_retries=2)

    with pytest.raises(DesignerError, match=r"busy \(529\)") as info:
        design_scenarios(login_page, client=api.client)

    assert not isinstance(info.value, DesignerFatalError)
    assert api.handler.call_count == 3  # first try + 2 SDK retries


def test_a_bad_api_key_is_fatal_and_not_retried(login_page):
    api = ScriptedAnthropic(api_error(401, "authentication_error"), max_retries=2)

    with pytest.raises(DesignerFatalError, match="API key"):
        design_scenarios(login_page, client=api.client)

    assert api.handler.call_count == 1  # retrying a 401 would only repeat it


def test_a_refusal_skips_the_page_without_retrying(login_page):
    api = ScriptedAnthropic(message("", stop_reason="refusal"))

    with pytest.raises(DesignerError, match="declined"):
        design_scenarios(login_page, client=api.client)

    assert api.handler.call_count == 1


# --------------------------------------- a page that fails does not stop the job

def test_a_page_that_keeps_failing_is_skipped_and_the_others_are_designed(login_page, contact_page):
    second = contact_page.__class__(url="https://shop.example/about", title="About", page_type="content", elements={})
    api = ScriptedAnthropic(
        message(VALID_SCENARIOS),                       # login: ok
        message("broken"), message("still broken"),     # contact: fails twice -> skipped
        message(VALID_SCENARIOS),                       # about: ok
    )

    outcomes = design_pages([login_page, contact_page, second], client=api.client)

    assert [o.result is not None for o in outcomes] == [True, False, True]
    assert "failed validation" in outcomes[1].error
    assert api.handler.call_count == 4


# ------------------------------------------------------------------ safety net

def test_a_call_nobody_mocked_cannot_reach_the_real_api(login_page):
    """conftest.py points every test at a fake key and a dead address (no tokens spent)."""
    client = designer._default_client()  # what the worker would use
    assert str(client.base_url).startswith("http://127.0.0.1:9")
    assert client.api_key == "sk-ant-test-not-a-real-key"

    with pytest.raises(DesignerError, match="Could not reach"):
        design_scenarios(login_page, client=client)
