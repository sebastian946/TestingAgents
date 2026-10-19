"""Error reporting with Sentry (WTA-30), for the API and the worker.

Off unless `SENTRY_DSN` is set. `send_default_pii=False` keeps emails, IPs and request
bodies out of the reports; what we need to debug is the stack trace and the job id.
"""
from config.variables import settings


def init_sentry(component: str) -> bool:
    if not settings.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        send_default_pii=False,
        traces_sample_rate=0.0,  # errors only; performance tracing is LangSmith's job
    )
    sentry_sdk.set_tag("component", component)
    return True
