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


def _streaming_client(chunks):
    """A client whose response body is produced lazily, chunk by chunk."""
    def handler(request):
        return httpx.Response(200, content=(c() if callable(c) else c
                                            for c in chunks))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_body_is_written_to_a_part_file_not_the_destination(tmp_path):
    """The destination path must never hold a partially written archive.

    The cache is trusted on sight — `if dest.exists(): return dest` never
    revalidates — so a process killed while writing directly to
    `2024q1.zip` would poison that quarter permanently, across every later
    build. Bytes therefore land in a `.part` sibling while in flight.
    """
    dest = tmp_path / "2024q1.zip"
    part = tmp_path / "2024q1.zip.part"
    seen = {}

    def midstream():
        seen["part_exists"] = part.exists()
        seen["dest_exists"] = dest.exists()
        return b"second"

    c = _streaming_client([b"first", midstream])
    p = download_archive(Quarter(2024, 1), tmp_path, client=c)

    assert seen["part_exists"] is True, "body was not streamed to .part"
    assert seen["dest_exists"] is False, "destination held a partial archive"
    assert p.read_bytes() == b"firstsecond"
    assert not part.exists()


def test_interrupted_download_leaves_no_zip_and_no_part(tmp_path):
    """An interruption mid-body must leave the cache as it found it."""
    def boom():
        raise httpx.ReadError("connection dropped mid-body")

    c = _streaming_client([b"first", boom])
    with pytest.raises(httpx.ReadError):
        download_archive(Quarter(2024, 1), tmp_path, client=c)

    assert not (tmp_path / "2024q1.zip").exists()
    assert not (tmp_path / "2024q1.zip.part").exists()
    assert list(tmp_path.iterdir()) == []


def test_retry_after_interruption_gets_the_whole_archive(tmp_path):
    """Because the interrupted attempt left nothing behind, the next run
    re-fetches rather than returning a truncated cached file."""
    def boom():
        raise httpx.ReadError("connection dropped mid-body")

    with pytest.raises(httpx.ReadError):
        download_archive(Quarter(2024, 1), tmp_path,
                         client=_streaming_client([b"first", boom]))
    p = download_archive(Quarter(2024, 1), tmp_path, client=_client(b"whole"))
    assert p.read_bytes() == b"whole"


def test_failed_response_leaves_no_part_file(tmp_path):
    c = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        download_archive(Quarter(1990, 1), tmp_path, client=c)
    assert list(tmp_path.iterdir()) == []
