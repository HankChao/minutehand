"""Ours (CHANGES-OURS.md): a fork may add something a person does by themselves after the checkpoint
(`AddHappening`), and may make every model call again from the checkpoint on (`Fork.live`).

An added happening is the scenario's own kind of happening, put at the end of the list so every happening the parent
scheduled keeps its place, and due the override's `after` past the checkpoint's moment. Nothing before the checkpoint
changes: the child shares its parent's log up to it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from minutehand.adapters.providers.google_workspace.provider import build
from minutehand.adapters.store.sqlite import SqliteStore
from minutehand.application.checkpoint import PendingHappening, checkpoints
from minutehand.application.orchestrator import Services, run_scenario
from minutehand.application.refusals import RunRefused
from minutehand.application.replier import PeopleReplier
from minutehand.application.restore import Played
from minutehand.application.rewind import changed_scenario, fork_run
from minutehand.application.run_clock import RunClock
from minutehand.application.sandbox import SandboxClock, SandboxReading
from minutehand.domain.agent import AgentUnderTest
from minutehand.domain.conversation import PersonCall, Wrote
from minutehand.domain.experiment import AddHappening, Fork
from minutehand.domain.run import RunRecord
from minutehand.domain.scenario import DocumentHappening, Scenario
from minutehand.domain.world import Actor, EntityKind, WorldEvent
from minutehand.ports.clock import Clock
from tests.support.scenarios import SANDBOX

SECOND = 1_000_000_000
SCENARIO = Scenario.model_validate(
    {
        "name": "added_happening",
        "starts_at": "2026-08-24T09:00:00Z",
        "runs_for": "P2D",
        "owner": "mara",
        "people": [
            {"key": "mara", "name": "Mara Lindqvist", "email": "mara@example.com", "reply": {"kind": "silent"}},
            {"key": "dov", "name": "Dov Aranha", "email": "dov@example.com", "reply": {"kind": "silent"}},
        ],
        "documents": [
            {
                "provider": "google_workspace",
                "title": "Launch plan",
                "text": "v1",
                "shared_with": [{"person": "dov", "role": "writer"}],
            }
        ],
        "happenings": [
            {
                "kind": "document",
                "person": "dov",
                "document": "Launch plan",
                "after": "PT6H",
                "action": {"kind": "edited", "append": "the parent's own edit"},
            }
        ],
    }
)
ADDED = {
    "kind": "add_happening",
    "after": "PT30M",
    "happening": {
        "kind": "document",
        "person": "dov",
        "document": "Launch plan",
        "action": {"kind": "edited", "append": "added by the fork"},
    },
}


class _Kernel(SandboxClock):
    """A sandbox that is always idle, its clock the run's as advanced."""

    def __init__(self, scenario: Scenario) -> None:
        super().__init__(SANDBOX)
        self._now_ns = int(scenario.starts_at.timestamp()) * SECOND

    async def read(self) -> SandboxReading:
        printed = {"idle": True, "now_ns": self._now_ns, "tasks": 1, "timed": 0, "earliest_ns": -1, "waits": []}
        return SandboxReading.model_validate_json(json.dumps(printed))

    async def advance(self, nanoseconds: int) -> None:
        self._now_ns += nanoseconds


class _Program:
    command: Sequence[str] = ("agent",)

    async def start(self, environment: Mapping[str, str] | None = None) -> None:
        del environment

    async def stop(self) -> None:
        return None


class _NothingToPlay:
    async def play(self, progress: object, *, start: object) -> Played:
        del progress, start
        return Played(calls_answered=0, deliveries_resent=0, jumps=0)


async def _parent(tmp_path: Path) -> tuple[RunRecord, int]:
    """The parent played to its window's end, and the seq of the checkpoint after its start."""
    clock = RunClock(SCENARIO.starts_at)
    store = SqliteStore(tmp_path / "world.db", "parent", clock)
    record = await run_scenario(
        scenario=SCENARIO,
        agent=AgentUnderTest(name="agent", sandbox=SANDBOX),
        sandbox=_Kernel(SCENARIO),
        store=store,
        clock=clock,
        services=Services(providers=[build()]),
        replier=PeopleReplier(SCENARIO, None, []),
    )
    at_seq = min(seq for seq, c in checkpoints(store).items() if c.wake == 1)
    store.close()
    return record, at_seq


async def _forked(tmp_path: Path, parent: RunRecord, at_seq: int, child: str, overrides: list[object]) -> RunRecord:
    def open_parent(clock: Clock) -> SqliteStore:
        return SqliteStore(tmp_path / "world.db", "parent", clock)

    [record] = await fork_run(
        fork=Fork.model_validate({"parent_run": "parent", "at_seq": at_seq, "overrides": overrides}),
        parent=parent,
        open_parent=open_parent,
        run_id=child,
        scenario=SCENARIO,
        agent=AgentUnderTest(name="agent", sandbox=SANDBOX),
        sandbox=_Kernel(SCENARIO),
        playback_for=lambda _store, _checkpoint: _NothingToPlay(),
        services=Services(providers=[build()]),
        replier_for=lambda scenario, _pins: PeopleReplier(scenario, None, []),
        state_dir=tmp_path / "state",
        own=_Program(),
    )
    return record


def _edits(tmp_path: Path, run: str) -> list[WorldEvent]:
    """Every change a person made to a document that `run` can see, in order."""
    store = SqliteStore(tmp_path / "world.db", run, RunClock(SCENARIO.starts_at))
    try:
        return [e for e in store.events(kinds=[EntityKind.DOCUMENT]) if e.actor is Actor.PERSON]
    finally:
        store.close()


def _said(events: Sequence[WorldEvent]) -> list[tuple[timedelta, str]]:
    return [(e.sim_time - SCENARIO.starts_at, e.after.text.rsplit("\n", 1)[-1]) for e in events if e.after is not None]  # type: ignore[union-attr]


async def test_an_added_happening_fires_its_after_past_the_checkpoint_and_the_parents_own_keeps_its_moment(
    tmp_path: Path,
) -> None:
    parent, at_seq = await _parent(tmp_path)
    assert _said(_edits(tmp_path, "parent")) == [(timedelta(hours=6), "the parent's own edit")]

    await _forked(tmp_path, parent, at_seq, "child", [ADDED])

    assert _said(_edits(tmp_path, "child")) == [
        (timedelta(minutes=30), "added by the fork"),
        (timedelta(hours=6), "the parent's own edit"),
    ]


async def test_nothing_before_the_checkpoint_changes_and_the_parent_is_left_as_it_was(tmp_path: Path) -> None:
    parent, at_seq = await _parent(tmp_path)

    def log(run: str, until: int | None = None) -> list[str]:
        store = SqliteStore(tmp_path / "world.db", run, RunClock(SCENARIO.starts_at))
        try:
            return [e.model_dump_json() for e in store.events(until=until)]
        finally:
            store.close()

    before, shared = log("parent"), log("parent", at_seq)
    await _forked(tmp_path, parent, at_seq, "child", [ADDED])

    assert log("child", at_seq) == shared, "the child shares the parent's log up to the checkpoint, event for event"
    assert log("parent") == before, "the parent's own log is not written to by its fork"


async def test_a_fork_that_adds_nothing_plays_as_a_fork_did_before(tmp_path: Path) -> None:
    parent, at_seq = await _parent(tmp_path)
    await _forked(tmp_path, parent, at_seq, "plain", [])
    assert _said(_edits(tmp_path, "plain")) == [(timedelta(hours=6), "the parent's own edit")]


async def test_several_added_happenings_each_fire_at_their_own_moment(tmp_path: Path) -> None:
    parent, at_seq = await _parent(tmp_path)
    again = {
        **ADDED,
        "after": "P1D",
        "happening": {**ADDED["happening"], "action": {"kind": "edited", "append": "a day on"}},
    }  # type: ignore[dict-item]
    await _forked(tmp_path, parent, at_seq, "twice", [ADDED, again])
    assert _said(_edits(tmp_path, "twice")) == [
        (timedelta(minutes=30), "added by the fork"),
        (timedelta(hours=6), "the parent's own edit"),
        (timedelta(days=1), "a day on"),
    ]


def test_an_added_happening_is_put_last_so_the_parents_happenings_keep_their_places() -> None:
    fork = Fork.model_validate({"parent_run": "parent", "at_seq": 1, "overrides": [ADDED]})
    changed = changed_scenario(SCENARIO, fork, SCENARIO.starts_at + timedelta(hours=2))
    assert changed.happenings[0] == SCENARIO.happenings[0]
    added = changed.happenings[1]
    assert isinstance(added, DocumentHappening) and added.after == timedelta(hours=2, minutes=30)
    assert PendingHappening.model_fields["happening"].description is not None  # the place is what a checkpoint keeps


def test_an_added_happening_with_a_moment_of_its_own_is_refused() -> None:
    with pytest.raises(ValidationError, match="counts from the scenario's start"):
        AddHappening.model_validate({**ADDED, "happening": {**ADDED["happening"], "after": "PT1H"}})  # type: ignore[dict-item]


def test_an_added_happening_at_the_checkpoints_own_moment_is_refused() -> None:
    with pytest.raises(ValidationError):
        AddHappening.model_validate({**ADDED, "after": "PT0S"})


def test_an_added_happening_by_someone_not_in_the_scenario_is_refused() -> None:
    stranger = {**ADDED, "happening": {**ADDED["happening"], "person": "nobody"}}  # type: ignore[dict-item]
    fork = Fork.model_validate({"parent_run": "parent", "at_seq": 1, "overrides": [stranger]})
    with pytest.raises(RunRefused, match="nobody"):
        changed_scenario(SCENARIO, fork, SCENARIO.starts_at)


def test_a_fork_made_afresh_is_answered_nothing_from_the_file_and_one_not_is(tmp_path: Path) -> None:
    """`Fork.live`: the people's and the judges' model calls are kept by their context (`Store.written`), so a
    second world asked the same would be answered the first's words. A child made afresh is answered None, and
    asks its model again; what it is answered is still kept."""
    clock = RunClock(SCENARIO.starts_at)
    parent = SqliteStore(tmp_path / "world.db", "parent", clock)
    parent.record_person_call(
        PersonCall(
            key="the same ask",
            person="dov",
            wrote=Wrote.REPLY,
            model="model-luna",
            prompt_version="1",
            sim_time=clock.now(),
            wake=0,
            answer='"then"',
        )
    )
    as_upstream = parent.fork("copying", at_seq=parent.head(), clock=RunClock(SCENARIO.starts_at))
    live = parent.fork("live", at_seq=parent.head(), clock=RunClock(SCENARIO.starts_at), afresh=True)
    assert as_upstream.written("the same ask") == '"then"'
    assert live.written("the same ask") is None


def test_a_fork_with_an_added_happening_reads_back_as_it_was_kept() -> None:
    """`fork.json` keeps the fork as validated, the happening's `after` filled in from the override's: reading it
    back (the fork's report, `minutehand findings`) must accept what it wrote. Found in a real run: the fork played
    to its end and its report failed to read its own `fork.json`."""
    fork = Fork.model_validate({"parent_run": "parent", "at_seq": 1, "overrides": [ADDED]})
    assert Fork.model_validate_json(fork.model_dump_json()) == fork


def test_a_fork_with_an_added_happening_and_live_is_said_in_its_report() -> None:
    """Found in a real run: the fork played to its end and its report's summary of the changes refused the added
    happening (`forks.short_words` knew none). Both wordings now say it, and a live fork says it is live."""
    from minutehand.application.forks import change_words, span, summary

    fork = Fork.model_validate({"parent_run": "parent", "at_seq": 1, "overrides": [ADDED], "live": True})
    said = summary(fork, SCENARIO)
    half = span(timedelta(minutes=30))
    assert said.startswith("live") and f"+{half}" in said and "Dov Aranha" in said
    assert f"{half} after the fork" in change_words(fork.overrides[0], SCENARIO)
