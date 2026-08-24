"""Unit tests for native_downloader.py — pure Python download engine."""

import asyncio
import hashlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from src.api.native_downloader import (
    CHUNK_SIZE,
    DEFAULT_SEGMENTS,
    MAX_SEGMENTS,
    SEGMENT_MIN_SIZE,
    DownloadStatus,
    DownloadTask,
    NativeDownloader,
    get_downloader,
)

# ─── Constants ────────────────────────────────────────────────────

class TestConstants:
    def test_chunk_size(self):
        assert CHUNK_SIZE == 8192

    def test_default_segments(self):
        assert DEFAULT_SEGMENTS == 4

    def test_max_segments(self):
        assert MAX_SEGMENTS == 16

    def test_segment_min_size(self):
        assert SEGMENT_MIN_SIZE == 1024 * 1024


# ─── DownloadStatus tests ─────────────────────────────────────────

class TestDownloadStatus:
    def test_all_values(self):
        assert DownloadStatus.PENDING == "pending"
        assert DownloadStatus.CONNECTING == "connecting"
        assert DownloadStatus.DOWNLOADING == "downloading"
        assert DownloadStatus.PAUSED == "paused"
        assert DownloadStatus.DONE == "done"
        assert DownloadStatus.ERROR == "error"
        assert DownloadStatus.CANCELLED == "cancelled"


# ─── DownloadTask tests ───────────────────────────────────────────

class TestDownloadTask:
    def test_default_values(self):
        task = DownloadTask(id="t1", url="http://x", dest="/tmp/f")
        assert task.status == DownloadStatus.PENDING
        assert task.total_bytes == 0
        assert task.downloaded_bytes == 0
        assert task.speed == 0
        assert task.eta == 0
        assert task.segments == DEFAULT_SEGMENTS
        assert task.supports_resume is False
        assert task.error is None
        assert task.plugin == "native"
        assert task.added_at > 0
        assert task.started_at is None
        assert task.completed_at is None

    def test_progress_pct_zero_total(self):
        task = DownloadTask(id="t1", url="x", dest="y")
        assert task.progress_pct == 0

    def test_progress_pct_half(self):
        task = DownloadTask(id="t1", url="x", dest="y", total_bytes=200, downloaded_bytes=100)
        assert task.progress_pct == 50.0

    def test_progress_pct_full(self):
        task = DownloadTask(id="t1", url="x", dest="y", total_bytes=100, downloaded_bytes=100)
        assert task.progress_pct == 100.0

    def test_progress_pct_over_100_capped(self):
        task = DownloadTask(id="t1", url="x", dest="y", total_bytes=100, downloaded_bytes=300)
        assert task.progress_pct == 100.0

    def test_to_dict(self):
        task = DownloadTask(id="t1", url="http://x", dest="/tmp/f", total_bytes=100, downloaded_bytes=50)
        d = task.to_dict()
        assert d["id"] == "t1"
        assert d["url"] == "http://x"
        assert d["dest"] == "/tmp/f"
        assert d["status"] == "pending"
        assert d["total_bytes"] == 100
        assert d["downloaded_bytes"] == 50
        assert d["progress_pct"] == 50.0
        assert d["segments"] == DEFAULT_SEGMENTS
        assert d["plugin"] == "native"


# ─── NativeDownloader tests ───────────────────────────────────────

class TestNativeDownloader:
    def test_init(self):
        dl = NativeDownloader(max_concurrent=5)
        assert dl._max_concurrent == 5
        assert dl._tasks == {}
        assert dl._running == {}

    def test_list_tasks_empty(self):
        dl = NativeDownloader()
        assert dl.list_tasks() == []

    def test_get_task_none(self):
        dl = NativeDownloader()
        assert dl.get_task("nonexistent") is None

    @pytest.mark.asyncio
    async def test_add_download(self):
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            task = await dl.add_download("http://example.com/file.bin", "/tmp/file.bin")
            assert task.status == DownloadStatus.PENDING
            assert task.url == "http://example.com/file.bin"
            assert task.dest == "/tmp/file.bin"
            assert dl.get_task(task.id) is task

    @pytest.mark.asyncio
    async def test_add_download_idempotent(self):
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock):
            t1 = await dl.add_download("http://x.com/f", "/tmp/f")
            t2 = await dl.add_download("http://x.com/f", "/tmp/f")
            assert t1.id == t2.id  # same URL/dest = same task

    @pytest.mark.asyncio
    async def test_add_download_caps_segments(self):
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock):
            task = await dl.add_download("http://x.com/f", "/tmp/f", segments=100)
            assert task.segments == MAX_SEGMENTS

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock):
            task = await dl.add_download("http://x.com/f", "/tmp/f")
            await dl.cancel(task.id)
            assert task.status == DownloadStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        dl = NativeDownloader()
        # Should not raise
        await dl.cancel("nonexistent")

    @pytest.mark.asyncio
    async def test_remove_task(self):
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock):
            task = await dl.add_download("http://x.com/f", "/tmp/f")
            await dl.remove(task.id)
            assert dl.get_task(task.id) is None

    @pytest.mark.asyncio
    async def test_pause_task(self):
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock):
            task = await dl.add_download("http://x.com/f", "/tmp/f")
            task.status = DownloadStatus.DOWNLOADING
            task._pause_event = asyncio.Event()
            await dl.pause(task.id)
            assert task.status == DownloadStatus.PAUSED

    @pytest.mark.asyncio
    async def test_resume_paused_task(self):
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock):
            task = await dl.add_download("http://x.com/f", "/tmp/f")
            task.status = DownloadStatus.PAUSED
            task._pause_event = asyncio.Event()
            task._pause_event.set()
            task._cancel_event = asyncio.Event()
            await dl.resume(task.id)
            assert task.status == DownloadStatus.DOWNLOADING

    @pytest.mark.asyncio
    async def test_download_sync_timeout(self):
        """Test that download_sync respects timeout."""
        dl = NativeDownloader()
        with patch.object(dl, "_run_download", new_callable=AsyncMock):
            task = await dl.add_download("http://x.com/f", "/tmp/f")
            # Simulate task staying pending forever
            task.status = DownloadStatus.PENDING
            with patch("asyncio.sleep", new_callable=AsyncMock):
                # Patch time.time to simulate timeout
                original_time = time.time()
                call_count = [0]
                def fake_time():
                    call_count[0] += 1
                    if call_count[0] > 2:
                        return original_time + 400  # past timeout
                    return original_time
                with patch("src.api.native_downloader.time.time", side_effect=fake_time):
                    with patch("src.api.native_downloader.asyncio.sleep", new_callable=AsyncMock):
                        result = await dl.download_sync("http://x.com/f", "/tmp/f")
                        # The task should be the same one that timed out
                        assert result.status in (DownloadStatus.ERROR, DownloadStatus.PENDING)


# ─── Singleton test ───────────────────────────────────────────────

class TestSingleton:
    def test_get_downloader(self):
        dl1 = get_downloader()
        dl2 = get_downloader()
        assert dl1 is dl2
        assert isinstance(dl1, NativeDownloader)
