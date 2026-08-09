from strategy_client.push import push


def test_strategy_client_uses_bearer_auth_and_expected_endpoint(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"created":true}'

    def open_request(request, timeout):
        captured.update(url=request.full_url, auth=request.headers["Authorization"], timeout=timeout)
        return Response()

    monkeypatch.setattr("strategy_client.push.urlopen", open_request)
    token = "strategy-token-with-at-least-thirty-two-characters"
    assert push("https://ciclotrade.com", "event", {"source": "test"}, token) == {"created": True}
    assert captured == {
        "url": "https://ciclotrade.com/api/v1/quant/events",
        "auth": f"Bearer {token}",
        "timeout": 20,
    }
