import httpx
import pytest


@pytest.fixture()
def fake_http(monkeypatch):
    """Route every `httpx.Client` created by the given module through a handler."""
    real_client = httpx.Client
    requests: list[httpx.Request] = []

    def install(module, handler):
        def recording(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(recording)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(module.httpx, "Client", factory)

    install.requests = requests
    return install
