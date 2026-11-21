"""Unit tests for load_versioned_json / save_versioned_json and the set-aside."""
import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.shared import persistence
from backend.shared.persistence import (
    SET_ASIDE_SUFFIX,
    load_versioned_json,
    load_versioned_json_sync,
    save_versioned_json,
    set_aside,
)


def _kept(file: Path):
    return sorted(file.parent.glob(f"{file.name}.*{SET_ASIDE_SUFFIX}"))


UNUSABLE = {
    "truncated": b'{"schema_version": 2, "x": 4',
    "empty": b"",
    "not_utf8": b"\xff\xfe{\x00}\x00",
    "not_an_object": b"[1, 2]",
    "no_version": json.dumps({"x": 42}).encode(),
    "older_version": json.dumps({"schema_version": 1, "x": 42}).encode(),
    "newer_version": json.dumps({"schema_version": 3, "x": 42}).encode(),
    "version_as_a_string": json.dumps({"schema_version": "2", "x": 42}).encode(),
    "required_key_missing": json.dumps({"schema_version": 2}).encode(),
    "too_deep": b"[" * 200_000,  # json.loads raises RecursionError, not a ValueError
}


@pytest.mark.asyncio
async def test_load_missing_file_returns_empty(tmp_path: Path):
    result = await load_versioned_json(tmp_path / "absent.json", expected_version=1)
    assert result == {}


@pytest.mark.asyncio
async def test_load_matching_schema_version_returns_data(tmp_path: Path):
    file = tmp_path / "ok.json"
    file.write_text(json.dumps({"schema_version": 2, "x": 42}), encoding="utf-8")

    data = await load_versioned_json(file, expected_version=2, required=("x",))
    assert data == {"schema_version": 2, "x": 42}
    assert _kept(file) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content", UNUSABLE.values(), ids=UNUSABLE.keys())
async def test_an_unusable_file_is_renamed_aside_and_read_as_absent(tmp_path: Path, content):
    """Renamed, not copied: the path is free for the owner's defaults, the
    bytes are kept exactly, and nothing is met again at the next boot."""
    file = tmp_path / "data.json"
    file.write_bytes(content)

    assert await load_versioned_json(file, expected_version=2, required=("x",)) == {}

    assert not file.exists()
    (kept,) = _kept(file)
    assert kept.read_bytes() == content
    assert await load_versioned_json(file, expected_version=2, required=("x",)) == {}
    assert len(_kept(file)) == 1


@pytest.mark.parametrize("content", UNUSABLE.values(), ids=UNUSABLE.keys())
def test_the_sync_loader_sets_aside_exactly_what_the_async_one_does(tmp_path: Path, content):
    """``SettingsService.get_setting_sync`` is the first reader of
    settings.json on every boot, before the async one. A twin that only
    *mostly* matched would put the one unchecked read back on the boot path —
    a shape nobody validated derived into the env files."""
    file = tmp_path / "data.json"
    file.write_bytes(content)

    assert load_versioned_json_sync(file, expected_version=2, required=("x",)) == {}

    assert not file.exists()
    (kept,) = _kept(file)
    assert kept.read_bytes() == content


def test_the_sync_loader_reads_a_valid_file_and_an_absent_one(tmp_path: Path):
    assert load_versioned_json_sync(tmp_path / "absent.json", expected_version=1) == {}
    current = tmp_path / "current.json"
    current.write_text(json.dumps({"schema_version": 2, "x": 42}), encoding="utf-8")
    assert load_versioned_json_sync(current, expected_version=2) == {
        "schema_version": 2,
        "x": 42,
    }


@pytest.mark.asyncio
async def test_a_second_set_aside_never_lands_on_the_first(tmp_path: Path, monkeypatch):
    """Two in the same second (a file the owner rewrote and that was damaged
    again): the stamp alone would name both the same, and a rename onto an
    existing name replaces it — the first copy, often the one worth having.
    Listed by name, they come in the order they were made."""
    frozen = time.gmtime(1_800_000_000)
    monkeypatch.setattr(
        persistence, "time", SimpleNamespace(gmtime=lambda *_: frozen, strftime=time.strftime, time=time.time)
    )
    file = tmp_path / "data.json"

    file.write_bytes(b"first")
    await load_versioned_json(file, expected_version=1)
    file.write_bytes(b"second")
    await load_versioned_json(file, expected_version=1)

    assert [k.read_bytes() for k in _kept(file)] == [b"first", b"second"]


@pytest.mark.asyncio
async def test_a_card_that_cannot_be_read_raises_and_moves_nothing(tmp_path: Path):
    """An I/O error says nothing about the bytes, which may be fine: setting
    them aside would cost a good file to a passing fault."""
    file = tmp_path / "data.json"
    file.mkdir()  # read_bytes → IsADirectoryError, an OSError like EIO

    with pytest.raises(OSError):
        await load_versioned_json(file, expected_version=1)

    assert file.exists() and _kept(file) == []


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_moved_aside_is_never_read_as_absent(tmp_path: Path, monkeypatch):
    """Read as absent, it would be overwritten by the owner's defaults at its
    first save, with no copy. The owner fails as on a card it cannot read,
    and its error says what was wrong with the file."""
    file = tmp_path / "data.json"
    file.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    def refused(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(persistence.os, "rename", refused)
    with pytest.raises(OSError, match="schema_version 1"):
        await load_versioned_json(file, expected_version=2)

    assert file.read_text(encoding="utf-8") == json.dumps({"schema_version": 1})


@pytest.mark.asyncio
async def test_a_file_another_reader_set_aside_first_is_reported_once(
    tmp_path: Path, monkeypatch, caplog
):
    """Two readers met the same bad file; the first renamed it. The second
    finds nothing to rename: absent, and its report would be a second one."""
    file = tmp_path / "data.json"
    file.write_bytes(b"{")

    def gone(src, dst):
        raise FileNotFoundError(src)

    monkeypatch.setattr(persistence.os, "rename", gone)
    with caplog.at_level(logging.ERROR):
        assert await load_versioned_json(file, expected_version=1) == {}

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.asyncio
async def test_a_set_aside_is_one_error_naming_the_file_its_copy_and_why(tmp_path: Path, caplog):
    """The only line the journal, errors.log and the live banner get: what
    was lost, where its bytes are, and why — from the one reporter."""
    file = tmp_path / "data.json"
    file.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        await load_versioned_json(file, expected_version=2)

    (record,) = caplog.records
    (kept,) = _kept(file)
    assert record.levelno == logging.ERROR and record.name == "backend.shared.persistence"
    message = record.getMessage()
    assert str(file) in message and kept.name in message and "schema_version 1" in message


@pytest.mark.asyncio
async def test_an_owner_sets_aside_what_only_it_can_refuse(tmp_path: Path, caplog):
    """A file that parses at the right version but holds a value its owner
    cannot take goes the same way, through the same report."""
    file = tmp_path / "equalizer.json"
    file.write_bytes(b'{"custom_gains": [null]}')

    with caplog.at_level(logging.WARNING):
        kept = await set_aside(file, "a gain that is not a number")

    assert not file.exists() and kept.read_bytes() == b'{"custom_gains": [null]}'
    (record,) = caplog.records
    assert record.name == "backend.shared.persistence" and kept.name in record.getMessage()
    assert await set_aside(file, "again") is None


@pytest.mark.asyncio
async def test_the_load_runs_off_the_loop_and_reports_on_it(tmp_path: Path, monkeypatch, caplog):
    """The read and the rename are blocking syscalls on an SD card, so they run
    on a worker; the report runs on the caller's loop, where anything it feeds
    — the live banner today — can broadcast without crossing threads."""
    file = tmp_path / "data.json"
    file.write_bytes(b"{")
    renamed_on = []
    real_rename = os.rename

    def recording_rename(src, dst):
        renamed_on.append(threading.current_thread())
        return real_rename(src, dst)

    monkeypatch.setattr(persistence.os, "rename", recording_rename)
    with caplog.at_level(logging.ERROR):
        await load_versioned_json(file, expected_version=1)

    assert renamed_on and threading.main_thread() not in renamed_on
    (record,) = caplog.records
    assert record.threadName == threading.main_thread().name


async def _load_it(file: Path):
    return await load_versioned_json(file, expected_version=1)


async def _set_it_aside(file: Path):
    return await set_aside(file, "a value its owner cannot take")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [_load_it, _set_it_aside], ids=["load", "set_aside"])
async def test_a_set_aside_its_caller_stopped_waiting_for_is_still_reported(
    entry, tmp_path: Path, monkeypatch, caplog
):
    """A load is cut by a stop during the boot, or by the STOP that cuts a
    source handler's read. The worker is not: its rename lands. Reported
    only by the caller that was cut, the bytes would be set aside with no word
    of it — and the next boot, finding no file, says nothing either."""
    file = tmp_path / "data.json"
    file.write_bytes(b"{")
    renaming, proceed = threading.Event(), threading.Event()
    real_rename = os.rename

    def slow_rename(src, dst):
        renaming.set()
        proceed.wait(5)
        return real_rename(src, dst)

    monkeypatch.setattr(persistence.os, "rename", slow_rename)
    with caplog.at_level(logging.ERROR):
        caller = asyncio.ensure_future(entry(file))
        assert await asyncio.to_thread(renaming.wait, 5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        proceed.set()
        for _ in range(500):
            if caplog.records:
                break
            await asyncio.sleep(0.01)

    (kept,) = _kept(file)
    (record,) = caplog.records
    assert record.name == "backend.shared.persistence" and kept.name in record.getMessage()


@pytest.mark.asyncio
async def test_save_stamps_version_and_roundtrip(tmp_path: Path):
    file = tmp_path / "roundtrip.json"
    await save_versioned_json(file, {"x": 42, "y": "hello"}, version=3)

    loaded = await load_versioned_json(file, expected_version=3)
    assert loaded == {"schema_version": 3, "x": 42, "y": "hello"}


@pytest.mark.asyncio
async def test_save_overrides_caller_schema_version(tmp_path: Path):
    """save_versioned_json always stamps its `version` arg, even if caller set one in the dict."""
    file = tmp_path / "override.json"
    await save_versioned_json(file, {"schema_version": 99, "x": 1}, version=2)

    loaded = await load_versioned_json(file, expected_version=2)
    assert loaded["schema_version"] == 2


@pytest.mark.asyncio
async def test_save_concurrent_writes_do_not_race_on_tempfile(tmp_path: Path):
    """Overlapping writes to the same path must not collide on a shared temp file.

    Regression guard for the EQ persist crash: a fixed ``<file>.tmp`` name lets the
    first writer's ``os.replace`` rename it away, so a concurrent writer's
    ``os.replace`` then raises ``FileNotFoundError [Errno 2] '<file>.tmp' -> '<file>'``.
    The local EQ record reaches this primitive from several uncoordinated paths
    (debounced persist + the access layer's ``persist_state``/``update_cache``), so
    concurrent writes are real. A unique temp name per write makes them collision-free.
    """
    file = tmp_path / "concurrent.json"

    # Many overlapping writers maximize the chance of hitting the race window on a
    # shared-tempfile implementation; a unique-tempfile implementation never collides.
    await asyncio.gather(*[
        save_versioned_json(file, {"writer": i}, version=1)
        for i in range(25)
    ])

    # The final file is a complete payload from exactly one writer (last wins).
    loaded = await load_versioned_json(file, expected_version=1)
    assert loaded["schema_version"] == 1
    assert "writer" in loaded

    # No stray temp files left behind.
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_save_does_not_block_the_event_loop(tmp_path: Path, monkeypatch):
    """The whole write sequence must run off the event-loop thread.

    mkdir, fsync and os.replace are blocking syscalls, and this primitive is on
    the write path of every persisted file. Doing them on the loop thread stalls
    every WS, HTTP and monitor task for as long as the SD card takes — the same
    class of freeze as the CD reader's blocking wait_ready. Asserting *where*
    the syscall ran rather than how long it took keeps this off the wall clock.
    """
    file = tmp_path / "offloaded.json"
    threads = []
    real_replace = os.replace

    def recording_replace(src, dst):
        threads.append(threading.current_thread())
        return real_replace(src, dst)

    monkeypatch.setattr(persistence.os, "replace", recording_replace)
    await save_versioned_json(file, {"x": 1}, version=1)

    assert threads, "os.replace was never called — the write did not happen"
    assert threading.main_thread() not in threads, (
        "the atomic replace ran on the event-loop thread"
    )


@pytest.mark.asyncio
async def test_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch):
    """A write that dies part-way must not leave its scratch file behind.

    The temp name carries a PID and a counter, so a leak is never overwritten:
    every failed write of a hot record (the EQ persist fires on every drag)
    would add one more file next to the real one, forever.
    """
    file = tmp_path / "doomed.json"

    def failing_replace(src, dst):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(persistence.os, "replace", failing_replace)

    with pytest.raises(OSError):
        await save_versioned_json(file, {"x": 1}, version=1)

    assert not file.exists()
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.fixture
def open_umask():
    """A permissive umask, so a private write is told apart from a default one:
    under a 077 umask every file is 0600 whatever the code asked for."""
    previous = os.umask(0o022)
    yield
    os.umask(previous)


@pytest.mark.asyncio
async def test_private_save_is_never_readable_by_others(tmp_path: Path, monkeypatch, open_umask):
    """A secret (a Spotify account's stored credentials) must be 0600 from the
    first byte written, not chmod-ed after the fact: a temp file created with
    the default mode is readable by every local user until the rename."""
    file = tmp_path / "secrets" / "profiles.json"
    modes_at_rename = []
    real_replace = os.replace

    def recording_replace(src, dst):
        modes_at_rename.append(os.stat(src).st_mode & 0o777)
        return real_replace(src, dst)

    monkeypatch.setattr(persistence.os, "replace", recording_replace)
    await save_versioned_json(file, {"x": 1}, version=1, private=True)

    assert modes_at_rename == [0o600]
    assert file.stat().st_mode & 0o777 == 0o600
    assert file.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_private_save_tightens_an_existing_readable_file(tmp_path: Path, open_umask):
    """go-librespot's state.json, or a file written before it held a secret,
    may already be 0644: the private write replaces it with a 0600 file rather
    than inheriting the old mode."""
    file = tmp_path / "state.json"
    file.write_text("{}", encoding="utf-8")
    file.chmod(0o644)

    await persistence.write_bytes_atomically(file, b'{"credentials": {}}', private=True)

    assert file.stat().st_mode & 0o777 == 0o600
    assert file.read_bytes() == b'{"credentials": {}}'
