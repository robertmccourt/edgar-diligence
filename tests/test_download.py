import httpx, pytest
from edgar.ingest.archives import Quarter, download_archive, RateLimiter

def _client(payload=b"zipbytes"):
    def handler(request):
        assert request.headers["user-agent"], "SEC requires a User-Agent"
        return httpx.Response(200, content=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_download_writes_file(tmp_path):
    p = download_archive(Quarter(2024, 1), tmp_path, client=_client())
    assert p.name == "2024q1.zip"
    assert p.read_bytes() == b"zipbytes"

def test_download_skips_existing(tmp_path):
    (tmp_path / "2024q1.zip").write_bytes(b"cached")
    p = download_archive(Quarter(2024, 1), tmp_path, client=_client(b"fresh"))
    assert p.read_bytes() == b"cached"

def test_download_raises_on_404(tmp_path):
    c = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        download_archive(Quarter(1990, 1), tmp_path, client=c)

def test_rate_limiter_spaces_calls():
    import time
    lim = RateLimiter(max_per_second=20)
    t0 = time.monotonic()
    for _ in range(3):
        lim.acquire()
    assert time.monotonic() - t0 >= 0.09
