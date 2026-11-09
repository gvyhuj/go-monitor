import io
import urllib.error

import pytest

import verify_publication as publication


@pytest.fixture
def built(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "live.bin").write_bytes(b"encrypted-new-publication")
    return tmp_path


def test_only_matching_served_bytes_confirm_publication(built, monkeypatch):
    calls, waits = [], []
    bodies = iter([b"encrypted-old-publication", b"encrypted-new-publication"])
    def fetch(request, **kwargs):
        calls.append(request)
        return io.BytesIO(next(bodies))
    monkeypatch.setattr(publication.urllib.request, "urlopen", fetch)
    monkeypatch.setattr(publication.time, "sleep", waits.append)
    publication.verify(built, "https://example.org/monitor/", attempts=3)
    assert len(calls) == 2 and waits == [5]
    assert calls[0].full_url != calls[1].full_url
    assert calls[0].get_header("Cache-control") == "no-cache"


def test_reported_deploy_success_is_not_enough_when_old_bytes_remain(built, monkeypatch):
    monkeypatch.setattr(publication.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"old"))
    monkeypatch.setattr(publication.time, "sleep", lambda *a: None)
    with pytest.raises(RuntimeError, match="did not serve"):
        publication.verify(built, "https://example.org/", attempts=2)


def test_connection_failure_is_not_mistaken_for_success(built, monkeypatch):
    def fail(*a, **k):
        raise urllib.error.URLError("unavailable")
    monkeypatch.setattr(publication.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="URLError"):
        publication.verify(built, "https://example.org/", attempts=1)


def test_empty_build_is_never_a_success(built):
    (built / "d" / "live.bin").write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        publication.verify(built, "https://example.org/")
