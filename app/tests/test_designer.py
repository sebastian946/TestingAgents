"""Unit tests for the Designer (WTA-15). A fake client stands in for the Anthropic API:
no network, no key, no cost. The real model is exercised by `agents.designer_eval`."""
from types import SimpleNamespace

import anthropic
import httpx2 as httpx  # anthropic 1.x is built on httpx2
import pytest
from pydantic import ValidationError

from agents import designer
from agents.designer import DesignerError, build_inventory, design_scenarios, load_prompt, render_prompt
from agents.designer_eval import CHECKOUT, LOGIN, check_description_effect, check_login
from models.agent_models import DesignerOutput, TestScenario


def _scenario(title, priority="high", category="negative", steps=("Open the page",)):
    return TestScenario(title=title, steps=list(steps), expected_result="An error is shown", priority=priority, category=category)


class FakeClient:
    """Records the parse() call and returns a canned response."""

    def __init__(self, scenarios=None, stop_reason="end_turn", error=None):
        self.calls = []
        output = DesignerOutput(scenarios=scenarios or [_scenario("Login fails with a wrong password")])
        usage = SimpleNamespace(input_tokens=1200, output_tokens=800, cache_read_input_tokens=None, cache_creation_input_tokens=1100)
        self._response = SimpleNamespace(stop_reason=stop_reason, parsed_output=output, model="claude-opus-5-5", usage=usage)
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def test_request_uses_structured_output_static_cached_system_and_no_temperature():
    client = FakeClient()
    result = design_scenarios(LOGIN, "A demo store", client=client, version="v2")

    call = client.calls[0]
    assert call["output_format"] is DesignerOutput
    assert "temperature" not in call  # rejected by current models
    assert call["fallbacks"] == "default" and call["betas"] == [designer.FALLBACK_BETA]
    assert call["output_config"] == {"effort": designer.settings.designer_effort}
    assert call["model"] == designer.settings.designer_model
    system_block = call["system"][0]
    assert system_block["cache_control"] == {"type": "ephemeral"}
    # Page data stays out of the system prompt, so the cached prefix is identical for every page
    assert "saucedemo" not in system_block["text"] and "A demo store" not in system_block["text"]
    assert result.scenarios[0].title == "Login fails with a wrong password"
    assert result.cache_read_input_tokens == 0 and result.cache_creation_input_tokens == 1100


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


def test_both_prompt_versions_load_and_unknown_version_fails():
    for version in ("v1", "v2"):
        system, user = load_prompt(version)
        assert system and "$inventory" in user.template
    with pytest.raises(DesignerError, match="Unknown designer prompt version"):
        load_prompt("v999")


@pytest.mark.parametrize("stop_reason, message", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_refusal_and_truncation_raise_designer_error(stop_reason, message):
    with pytest.raises(DesignerError, match=message):
        design_scenarios(LOGIN, client=FakeClient(stop_reason=stop_reason))


def test_bad_api_key_becomes_readable_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    with pytest.raises(DesignerError, match="API key is missing or invalid"):
        design_scenarios(LOGIN, client=FakeClient(error=error))


def test_schema_rejects_out_of_rubric_values():
    with pytest.raises(ValidationError):
        TestScenario(title="t", steps=["s"], expected_result="r", priority="urgent", category="negative")
    with pytest.raises(ValidationError):
        TestScenario(title="t", steps=["s"], expected_result="r", priority="high", category="performance")
    with pytest.raises(ValidationError):
        DesignerOutput.model_validate({"scenarios": [], "extra": 1})


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
