from __future__ import annotations

import hashlib

import pytest

from youtube_cleaner.network import (
    NetworkMutationError,
    YOUTUBE_ORIGIN,
    YouTubeNetworkClient,
    sapisid_authorization,
)


def test_sapisid_authorization_uses_all_available_session_cookies():
    cookies = [
        {"name": "SAPISID", "value": "primary"},
        {"name": "__Secure-1PAPISID", "value": "first-party"},
        {"name": "__Secure-3PAPISID", "value": "third-party"},
    ]
    timestamp = "1"

    value = sapisid_authorization(cookies, now=lambda: 1)

    expected = []
    for scheme, secret in (
        ("SAPISIDHASH", "primary"),
        ("SAPISID1PHASH", "first-party"),
        ("SAPISID3PHASH", "third-party"),
    ):
        digest = hashlib.sha1(
            f"{timestamp} {secret} {YOUTUBE_ORIGIN}".encode()
        ).hexdigest()
        expected.append(f"{scheme} {timestamp}_{digest}")
    assert value == " ".join(expected)


def test_sapisid_authorization_requires_an_authenticated_cookie():
    with pytest.raises(NetworkMutationError, match="SAPISID"):
        sapisid_authorization([], now=lambda: 1)


class FakeResponse:
    ok = True
    status = 200

    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


class FakeRequest:
    def __init__(self):
        self.calls = []
        self.response = FakeResponse()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FakeContext:
    def __init__(self):
        self.request = FakeRequest()

    def cookies(self, urls):
        assert urls == [YOUTUBE_ORIGIN]
        return [{"name": "SAPISID", "value": "secret"}]


class FakePage:
    url = "https://www.youtube.com/playlist?list=PLfixture"

    def __init__(self):
        self.context = FakeContext()

    def evaluate(self, script):
        return {
            "context": {"client": {"clientName": "WEB"}},
            "clientName": 1,
            "clientVersion": "2.test",
            "sessionIndex": 0,
            "visitorData": "visitor",
        }


@pytest.mark.parametrize(
    ("invoke", "endpoint", "mutation"),
    [
        (
            lambda client: client.remove_saved_playlist("PLsaved"),
            "like/removelike",
            {"target": {"playlistId": "PLsaved"}},
        ),
        (
            lambda client: client.delete_owned_playlist("PLowned"),
            "playlist/delete",
            {
                "playlistId": "PLowned",
                "deletePlaylistContext": "DELETE_PLAYLIST_CONTEXT_LIBRARY",
            },
        ),
    ],
)
def test_network_mutations_match_captured_request_shapes(invoke, endpoint, mutation):
    page = FakePage()

    invoke(YouTubeNetworkClient(page))

    [(url, request)] = page.context.request.calls
    assert url == f"{YOUTUBE_ORIGIN}/youtubei/v1/{endpoint}?prettyPrint=false"
    assert request["data"] == {"context": {"client": {"clientName": "WEB"}}, **mutation}
    assert request["headers"]["authorization"].startswith("SAPISIDHASH ")
    assert request["headers"]["referer"] == page.url
    assert request["headers"]["x-youtube-client-name"] == "1"
    assert request["headers"]["x-youtube-client-version"] == "2.test"
    assert request["headers"]["x-goog-visitor-id"] == "visitor"
    assert page.context.request.response.disposed
