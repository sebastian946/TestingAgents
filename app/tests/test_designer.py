"""Unit tests for the Designer (WTA-15, WTA-16). A fake client stands in for the Anthropic API:
no network, no key, no cost. The real model is exercised by `agents.designer_eval`."""
import json
from types import SimpleNamespace

import anthropic
import httpx2 as httpx  # anthropic 1.x is built on httpx2
import pytest
from pydantic import ValidationError

from agents import designer
from agents.designer import (
    DesignerError,
    DesignerFatalError,
    build_inventory,
    design_pages,
    design_scenarios,
    load_prompt,
    model_profile,
    render_prompt,
)
from agents.designer_eval import CHECKOUT, CONTACT_FORM, LOGIN, check_description_effect, check_login
from models.agent_models import DesignerOutput, TestScenario


def _scenario(title, priority="high", category="negative", steps=("Open the page",)):
    return TestScenario(title=title, steps=list(steps), expected_result="An error is shown", priority=priority, category=category)


def _valid_json(*titles: str) -> str:
    titles = titles or ("Login fails with a wrong password",)
    return DesignerOutput(scenarios=[_scenario(t) for t in titles]).model_dump_json()


def _response(text: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5"):
    usage = SimpleNamespace(input_tokens=1200, output_tokens=800, cache_read_input_tokens=None, cache_creation_input_tokens=1100)
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)], model=model, usage=usage)


def _api_error(cls, status: int):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("simulated", response=httpx.Response(status, request=request), body=None)


class FakeClient:
    """Records each create() call and replays a script of responses or exceptions."""

    def __init__(self, *script):
        self.calls: list[dict] = []
        self._script = list(script) or [_response(_valid_json())]
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return item


# ------------------------------------------------------------------- request (WTA-15)

def test_request_uses_structured_output_static_cached_system_and_no_temperature():
    client = FakeClient()
    result = design_scenarios(LOGIN, "A demo store", client=client, version="v2", model="claude-opus-5-5")

    call = client.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == designer.settings.designer_effort
    assert "temperature" not in call  # rejected by current models
    assert call["fallbacks"] == "default" and call["betas"] == [designer.FALLBACK_BETA]
    system_block = call["system"][0]
    assert system_block["cache_control"] == {"type": "ephemeral"}
    # Page data stays out of the system prompt, so the cached prefix is identical for every page
    assert "saucedemo" not in system_block["text"] and "A demo store" not in system_block["text"]
    assert result.scenarios[0].title == "Login fails with a wrong password"
    assert result.attempts == 1
    assert result.cache_read_input_tokens == 0 and result.cache_creation_input_tokens == 1100


def test_request_only_sends_what_the_model_accepts():
    haiku = FakeClient()
    design_scenarios(LOGIN, client=haiku, model="claude-haiku-4-5")
    call = haiku.calls[0]
    assert "effort" not in call["output_config"]  # Haiku 4.5 rejects effort
    assert "fallbacks" not in call and "betas" not in call
    assert call["output_config"]["format"]["type"] == "json_schema"

    sonnet = FakeClient()
    design_scenarios(LOGIN, client=sonnet, model="claude-sonnet-5-5")
    assert sonnet.calls[0]["fallbacks"] == "default" and "effort" in sonnet.calls[0]["output_config"]

    unknown = FakeClient()
    design_scenarios(LOGIN, client=unknown, model="claude-some-future-model")
    assert "effort" not in unknown.calls[0]["output_config"] and "fallbacks" not in unknown.calls[0]


def test_system_prompt_is_identical_across_pages():
    system_login, _ = render_prompt(LOGIN, None, "v2")
    system_checkout, _ = render_prompt(CHECKOUT, "Checkout is critical", "v2")
    assert system_login == system_checkout


def test_user_message_carries_page_type_inventory_and_description():
    _, user = render_prompt(LOGIN, "Tienda online. Lo crítico es el checkout.", "v2")
    assert "Heuristic page type: login" in user
    assert '"type":"password"' in user
    assert "Lo crítico es el checkout" in user
    assert "<page_inventory>" in user  # third-party content is fenced as data
    _, user_no_desc = render_prompt(LOGIN, "   ", "v2")
    assert designer.NO_DESCRIPTION in user_no_desc


def test_inventory_drops_classifier_signals_and_is_compact():
    inventory = build_inventory({"url": "u", "title": "t", "signals": {"price_count": 3}, "buttons": ["Buy"]})
    assert inventory == '{"buttons":["Buy"]}'
    assert "no interactive elements" in build_inventory({"signals": {"word_count": 10}})


def test_both_prompt_versions_load_and_unknown_version_is_fatal():
    for version in ("v1", "v2"):
        system, user = load_prompt(version)
        assert system and "$inventory" in user.template
    with pytest.raises(DesignerFatalError, match="Unknown designer prompt version"):
        load_prompt("v999")


# ------------------------------------------------------- self-healing retry (WTA-16)

def test_invalid_json_triggers_one_retry_with_validation_feedback():
    client = FakeClient(_response('{"scenarios": [{"title": "Login fails"'), _response(_valid_json()))
    result = design_scenarios(LOGIN, client=client)

    assert len(client.calls) == 2 and result.attempts == 2
    retry_message = client.calls[1]["messages"][0]["content"]
    assert retry_message.startswith(client.calls[0]["messages"][0]["content"])  # same task...
    assert "<validation_errors>" in retry_message and "Invalid JSON" in retry_message  # ...plus what was wrong
    assert '{"scenarios": [{"title": "Login fails"' in retry_message
    assert len(client.calls[1]["messages"]) == 1  # fresh single-turn request, no replayed assistant turn
    # usage of both attempts is accounted for
    assert result.input_tokens == 2400 and result.output_tokens == 1600


def test_schema_rule_violation_triggers_retry_naming_the_field():
    empty_steps = json.dumps({"scenarios": [{"title": "Login fails", "steps": [], "expected_result": "Error",
                                             "priority": "high", "category": "negative"}]})
    client = FakeClient(_response(empty_steps), _response(_valid_json()))
    design_scenarios(LOGIN, client=client)
    feedback = client.calls[1]["messages"][0]["content"]
    assert "scenarios.0.steps" in feedback and "at least 1 item" in feedback


def test_duplicate_titles_trigger_retry():
    duplicate = json.dumps({"scenarios": [_scenario("Same").model_dump(), _scenario("same").model_dump()]})
    client = FakeClient(_response(duplicate), _response(_valid_json("A", "B")))
    result = design_scenarios(LOGIN, client=client)
    assert "duplicate scenario title" in client.calls[1]["messages"][0]["content"]
    assert [s.title for s in result.scenarios] == ["A", "B"]


def test_two_invalid_responses_give_up_on_the_page():
    client = FakeClient(_response("not json at all"))
    with pytest.raises(DesignerError, match="failed validation after 2 attempts") as info:
        design_scenarios(LOGIN, client=client)
    assert not isinstance(info.value, DesignerFatalError)
    assert len(client.calls) == 2  # exactly one retry, not a loop


@pytest.mark.parametrize("stop_reason, message", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_refusal_and_truncation_are_page_errors_without_retry(stop_reason, message):
    client = FakeClient(_response("", stop_reason=stop_reason))
    with pytest.raises(DesignerError, match=message):
        design_scenarios(LOGIN, client=client)
    assert len(client.calls) == 1


# ---------------------------------------------------------- API errors (WTA-16)

@pytest.mark.parametrize("error, message", [
    (_api_error(anthropic.RateLimitError, 429), "busy \\(429\\)"),
    (_api_error(anthropic.OverloadedError, 529), "busy \\(529\\)"),
    (_api_error(anthropic.InternalServerError, 500), "failed \\(500\\)"),
    (anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com")), "timed out"),
])
def test_transient_errors_after_sdk_retries_are_page_errors(error, message):
    with pytest.raises(DesignerError, match=message) as info:
        design_scenarios(LOGIN, client=FakeClient(error))
    assert not isinstance(info.value, DesignerFatalError)


@pytest.mark.parametrize("error, message", [
    (_api_error(anthropic.AuthenticationError, 401), "API key is missing or invalid"),
    (_api_error(anthropic.NotFoundError, 404), "Unknown model"),
    (_api_error(anthropic.BadRequestError, 400), "rejected"),
])
def test_configuration_errors_are_fatal(error, message):
    with pytest.raises(DesignerFatalError, match=message):
        design_scenarios(LOGIN, client=FakeClient(error))


def test_default_client_gets_retries_and_timeout_from_settings(monkeypatch):
    monkeypatch.setattr(designer.settings, "anthropic_api_key", "sk-ant-test")
    client = designer._default_client()
    assert client.max_retries == designer.settings.designer_max_retries
    assert client.timeout == designer.settings.designer_timeout


# --------------------------------------------- one page failing does not stop the job

def test_a_failing_page_does_not_stop_the_others():
    client = FakeClient(
        _response(_valid_json("Login works")),                                   # page 1: ok
        _response("broken"), _response("still broken"),                          # page 2: fails twice
        _response(_valid_json("Checkout works")),                                # page 3: ok
    )
    outcomes = design_pages([LOGIN, CONTACT_FORM, CHECKOUT], client=client)

    assert [o.page.url for o in outcomes] == [LOGIN.url, CONTACT_FORM.url, CHECKOUT.url]
    assert outcomes[0].result.scenarios[0].title == "Login works" and outcomes[0].error is None
    assert outcomes[1].result is None and "failed validation" in outcomes[1].error
    assert outcomes[2].result.scenarios[0].title == "Checkout works"


def test_a_fatal_error_stops_the_run_instead_of_repeating_it_on_every_page():
    client = FakeClient(_api_error(anthropic.AuthenticationError, 401))
    with pytest.raises(DesignerFatalError):
        design_pages([LOGIN, CONTACT_FORM, CHECKOUT], client=client)
    assert len(client.calls) == 1


# -------------------------------------------------------------- schema and model choice

def test_schema_rejects_out_of_rubric_values_and_blank_text():
    with pytest.raises(ValidationError):
        TestScenario(title="t", steps=["s"], expected_result="r", priority="urgent", category="negative")
    with pytest.raises(ValidationError):
        TestScenario(title="t", steps=["s"], expected_result="r", priority="high", category="performance")
    with pytest.raises(ValidationError):
        TestScenario(title="   ", steps=["s"], expected_result="r", priority="high", category="negative")
    with pytest.raises(ValidationError):
        TestScenario(title="t", steps=["  "], expected_result="r", priority="high", category="negative")
    with pytest.raises(ValidationError):
        DesignerOutput.model_validate({"scenarios": []})
    with pytest.raises(ValidationError):
        DesignerOutput.model_validate({"scenarios": [], "extra": 1})


def test_cost_estimate_uses_model_prices():
    result = design_scenarios(LOGIN, client=FakeClient(_response(_valid_json(), model="claude-sonnet-5-5")), model="claude-sonnet-5-5")
    # 1200 in + 1.25*1100 cache write at $2/MTok + 800 out at $10/MTok
    assert result.estimated_cost_usd == pytest.approx((1200 + 1375) * 2 / 1e6 + 800 * 10 / 1e6)
    assert model_profile("claude-haiku-4-5").supports_effort is False


# ------------------------------------------------------------------- eval checks

def test_eval_login_checks_accept_good_output_and_flag_bad_priorities():
    good = [
        _scenario("Successful login with valid credentials", "critical", "functional"),
        _scenario("Login fails with an invalid password", "high"),
        _scenario("Login is rejected when both fields are empty", "high", "validation"),
    ]
    assert all(check_login(good).values())
    low_priority = [*good[:1], _scenario("Login fails with an invalid password", "low"), good[2]]
    assert not check_login(low_priority)["login: invalid credentials are high/critical"]
    spanish = [
        _scenario("Inicio de sesión exitoso con credenciales válidas", "critical", "functional"),
        _scenario("El login falla con contraseña inválida", "critical"),
        _scenario("No permite ingresar con los campos vacíos", "high", "validation"),
    ]
    assert all(check_login(spanish).values())


def test_eval_description_check():
    plain = [_scenario("a", "medium"), _scenario("b", "high")]
    assert check_description_effect(plain, [_scenario("a", "high"), _scenario("b", "critical")])["description: 'checkout is critical' raises priority or depth"]
    assert not check_description_effect(plain, list(plain))["description: 'checkout is critical' raises priority or depth"]


def test_eval_cli_runs_saves_and_compares_without_the_network(tmp_path, monkeypatch, capsys):
    from agents import designer_eval

    monkeypatch.setattr(designer_eval.settings, "reports_dir", str(tmp_path))
    good_login = [
        _scenario("Successful login with valid credentials", "critical", "functional"),
        _scenario("Login fails with an invalid password", "high"),
        _scenario("Login is rejected when both fields are empty", "high", "validation"),
    ]

    def fake_design(page, description, **kwargs):
        scenarios = good_login if page is LOGIN else [_scenario("Order is placed", "critical" if description else "high")]
        return designer.DesignResult(scenarios, kwargs["model"], 1, 2000, 1500, 0, 0)

    monkeypatch.setattr(designer_eval, "design_scenarios", fake_design)
    for model in ("claude-opus-5-5", "claude-haiku-4-5"):
        monkeypatch.setattr("sys.argv", ["designer_eval", "--yes", "--model", model])
        assert designer_eval.main() == 0  # all checks pass

    saved = sorted(p.name for p in (tmp_path / "designer_eval").glob("*.json"))
    assert len(saved) == 2 and any("claude-opus-5-5" in n for n in saved)
    haiku_run = json.loads(next((tmp_path / "designer_eval").glob("*haiku*.json")).read_text(encoding="utf-8"))
    assert haiku_run["effort"] is None  # not sent to Haiku 4.5

    monkeypatch.setattr("sys.argv", ["designer_eval", "--compare"])
    capsys.readouterr()
    assert designer_eval.main() == 0
    table = capsys.readouterr().out
    assert "claude-opus-5-5" in table and "claude-haiku-4-5" in table and "6/6" in table
