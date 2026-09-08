from starlette.requests import Request

from retium.server import local_membership_request


def request(host="localhost:8780", client="172.18.0.1", **headers):
    return Request({"type": "http", "scheme": "http", "path": "/api/team/members",
                    "query_string": b"", "server": ("localhost", 8780),
                    "client": (client, 1234),
                    "headers": [(key.encode(), value.encode()) for key, value in
                                {"host": host, **headers}.items()]})


def test_docker_forward_requires_explicit_local_only_configuration(monkeypatch):
    monkeypatch.delenv("RETICOM_LOCAL_PORT_FORWARD", raising=False)
    assert not local_membership_request(request())
    assert local_membership_request(request(client="127.0.0.1"))
    monkeypatch.setenv("RETICOM_LOCAL_PORT_FORWARD", "1")
    assert local_membership_request(request())
    assert local_membership_request(request(origin="http://localhost:8780"))
    assert not local_membership_request(request(host="remote.example:8780"))
    assert not local_membership_request(request(origin="https://evil.example"))
    assert not local_membership_request(request(**{"sec-fetch-site": "cross-site"}))
