from data.problems import describe
from data.sectors_client import SectorsAuthError, SectorsRateLimitError


class AuthenticationError(Exception):  # same name the anthropic/openai SDKs use
    pass


class RateLimitError(Exception):
    pass


def test_known_failures_get_specific_codes():
    assert describe(SectorsAuthError(401, "bad key")).code == "sectors_key_invalid"
    assert describe(SectorsRateLimitError(429, "slow down")).code == "sectors_rate_limited"
    assert describe(AuthenticationError("401")).code == "model_key_invalid"
    assert describe(RuntimeError("ANTHROPIC_API_KEY is not set — cannot build model 'x'")).code == "model_key_missing"
    assert describe(RateLimitError("429 insufficient_quota")).code == "model_out_of_credit"  # quota beats generic 429


def test_wrapped_cause_is_found_and_unknown_falls_back():
    wrapper = RuntimeError("event loop failed")
    wrapper.__cause__ = AuthenticationError("401")
    assert describe(wrapper).code == "model_key_invalid"
    assert describe(ValueError("boom")).code == "unexpected_error"


def test_plan_limit_is_not_blamed_on_the_key():
    body = '{"error":"SUBSCRIPTION_DOES_NOT_ALLOW","message":"Your current subscription does not allow this request."}'
    assert describe(SectorsAuthError(401, body)).code == "sectors_plan_limit"
