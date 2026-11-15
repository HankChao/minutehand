"""Ours (CHANGES-OURS.md 5): while a fork's agent is played back, its memory is answered as the parent's log stood at
a cursor that moves with the calls answered from the record. A write of the agent's moves no cursor, so a read right
after it, in the same wake, was answered without it: the agent's next model call then differed from the record's,
and every fork of a 30-day run whose agent noted something and read its notes in one wake was refused."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from minutehand.adapters.store.sqlite import SqliteStore
from minutehand.adapters.telemetry.receiver import _Playback  # pyright: ignore[reportPrivateUsage]
from minutehand.application import memory
from minutehand.application.run_clock import RunClock
from minutehand.domain.memory import Found, Listed, MemoryDelete, MemoryGet, MemoryList, MemoryPut, MemoryWrite, Written

START = datetime(2026, 8, 24, 9, tzinfo=UTC)


def _parent(tmp_path: Path) -> tuple[SqliteStore, int]:
    """A parent whose agent kept one note before the cursor and wrote a second after it."""
    store = SqliteStore(tmp_path / "world.db", "parent", RunClock(START))
    memory.remember(
        store, MemoryWrite(writes=[MemoryPut(key="notes/0001", value={"note": "before"})]), memory.Versions()
    )
    cursor = store.head()
    memory.remember(store, MemoryWrite(writes=[MemoryPut(key="notes/0002", value={"note": "mine"})]), memory.Versions())
    return store, cursor


def test_a_played_back_agent_reads_what_it_just_wrote(tmp_path: Path) -> None:
    parent, cursor = _parent(tmp_path)
    played = _Playback(parent, lambda: cursor)
    first = played.answer(MemoryList(prefix="notes/"))
    assert isinstance(first, Listed) and [i.key for i in first.items] == ["notes/0001"]

    wrote = played.answer(MemoryWrite(writes=[MemoryPut(key="notes/0002", value={"note": "mine"})]))
    assert isinstance(wrote, Written) and wrote.applied == [True]
    after = played.answer(MemoryList(prefix="notes/"))
    assert isinstance(after, Listed) and [(i.key, i.value) for i in after.items] == [
        ("notes/0001", {"note": "before"}),
        ("notes/0002", {"note": "mine"}),
    ], "the agent reads back the note it wrote this wake, as the parent's agent did"
    assert played.written[("default", "notes/0002")] == 1


def test_a_played_back_delete_is_gone_on_the_next_read(tmp_path: Path) -> None:
    parent, cursor = _parent(tmp_path)
    played = _Playback(parent, lambda: cursor)
    played.answer(MemoryWrite(writes=[MemoryDelete(key="notes/0001")]))
    got = played.answer(MemoryGet(key="notes/0001"))
    assert isinstance(got, Found) and got.found is False


def test_once_the_cursor_passes_the_write_the_log_answers_and_nothing_is_written(tmp_path: Path) -> None:
    parent, cursor = _parent(tmp_path)
    at = [cursor]
    played = _Playback(parent, lambda: at[0])
    played.answer(MemoryWrite(writes=[MemoryPut(key="notes/0003", value={"note": "played only"})]))
    at[0] = parent.head()  # a call of the agent's answered from the record moves the cursor past the parent's write
    listed = played.answer(MemoryList(prefix="notes/"))
    assert isinstance(listed, Listed) and [i.key for i in listed.items] == ["notes/0001", "notes/0002"]
    assert len(memory.memory_of(parent.events())) == 2, "nothing the played-back agent writes reaches the log"


def test_the_model_timeout_is_read_from_the_environment_and_a_bad_one_is_refused(monkeypatch: object) -> None:
    """Ours (CHANGES-OURS.md 6): a self-hosted model judging a month-long run needs more than two minutes."""
    import pytest

    from minutehand.adapters.model.pydantic_ai import TIMEOUT_SECONDS, TIMEOUT_VARIABLE, timeout_seconds
    from minutehand.application.refusals import RunRefused

    mp = pytest.MonkeyPatch()
    try:
        mp.delenv(TIMEOUT_VARIABLE, raising=False)
        assert timeout_seconds() == TIMEOUT_SECONDS
        mp.setenv(TIMEOUT_VARIABLE, "1800")
        assert timeout_seconds() == 1800.0
        for bad in ("0", "-5", "soon"):
            mp.setenv(TIMEOUT_VARIABLE, bad)
            with pytest.raises(RunRefused, match=TIMEOUT_VARIABLE):
                timeout_seconds()
    finally:
        mp.undo()
