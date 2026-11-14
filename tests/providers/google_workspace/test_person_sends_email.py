"""Ours (CHANGES-OURS.md 4): a person sends an email by themselves at a moment (`PersonEmails`, `sends_email`): a
new unread message lands in each recipient's mailbox, the person's, recorded as theirs, and wakes nobody. With
`add_happening` a fork sends one after its checkpoint: an email arriving mid-run, which upstream's Gmail could only
seed before the start or write as a reply to the agent."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from minutehand.adapters.providers.google_workspace import gmail_wire
from minutehand.adapters.providers.google_workspace.gmail import MailWorld
from minutehand.adapters.providers.google_workspace.provider import build
from minutehand.adapters.store.sqlite import SqliteStore
from minutehand.application.checkpoint import checkpoints
from minutehand.application.orchestrator import Services, run_scenario
from minutehand.application.refusals import RunRefused
from minutehand.application.replier import PeopleReplier
from minutehand.application.restore import Played
from minutehand.application.rewind import fork_run
from minutehand.application.run_clock import RunClock
from minutehand.application.sandbox import SandboxClock, SandboxReading
from minutehand.domain.agent import AgentUnderTest
from minutehand.domain.experiment import Fork
from minutehand.domain.run import RunRecord
from minutehand.domain.scenario import Scenario
from minutehand.domain.world import Actor, EntityKind, MessageSnapshot, WorldEvent
from minutehand.ports.clock import Clock
from tests.support.scenarios import SANDBOX

SECOND = 1_000_000_000
BASE = {
    "name": "mail_midway",
    "starts_at": "2026-08-24T09:00:00Z",
    "runs_for": "P2D",
    "owner": "ava",
    "people": [
        {"key": "ava", "name": "Ava Lin", "email": "ava@example.com", "reply": {"kind": "silent"}},
        {"key": "kevin", "name": "Kevin Chen", "email": "kevin@outside.example", "reply": {"kind": "silent"}},
    ],
    "provider_seeds": [{"provider": "google_workspace", "body": {"unknown_credentials_act_as": "ava"}}],
}
EMAIL = {
    "kind": "sends_email",
    "provider": "google_workspace",
    "person": "kevin",
    "after": "PT6H",
    "from_address": "kevin@look-alike.example",
    "subject": "Q3 numbers",
    "text": "Please send me the Q3 report today.",
}


class _Kernel(SandboxClock):
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


async def _run(tmp_path: Path, scenario: Scenario, run: str = "parent") -> tuple[RunRecord, SqliteStore]:
    clock = RunClock(scenario.starts_at)
    store = SqliteStore(tmp_path / "world.db", run, clock)
    record = await run_scenario(
        scenario=scenario,
        agent=AgentUnderTest(name="agent", sandbox=SANDBOX),
        sandbox=_Kernel(scenario),
        store=store,
        clock=clock,
        services=Services(providers=[build()]),
        replier=PeopleReplier(scenario, None, []),
    )
    return record, store


def _landed(events: Sequence[WorldEvent]) -> list[WorldEvent]:
    return [
        e
        for e in events
        if e.actor is Actor.PERSON and e.entity.kind is EntityKind.MESSAGE and isinstance(e.after, MessageSnapshot)
    ]


async def test_an_email_lands_unread_in_the_owners_inbox_at_its_moment_as_the_persons(tmp_path: Path) -> None:
    scenario = Scenario.model_validate({**BASE, "happenings": [EMAIL]})
    record, store = await _run(tmp_path, scenario)

    [landed] = _landed(store.events())
    assert landed.sim_time == scenario.starts_at + timedelta(hours=6)
    assert isinstance(landed.after, MessageSnapshot)
    assert landed.after.text == "Q3 numbers\n\nPlease send me the Q3 report today."
    inbox = MailWorld(store).owner("ava@example.com")
    assert inbox is not None
    [held] = [m for m, _ in MailWorld(store).messages(inbox) if gmail_wire.UNREAD in m.labels]
    sent = base64.urlsafe_b64decode(held.raw + "==").decode()  # Gmail keeps the RFC 2822 message base64url
    assert "From: Kevin Chen <kevin@look-alike.example>" in sent and "To: Ava Lin <ava@example.com>" in sent
    assert [w.reason for w in record.wakes] == ["start"], "an email wakes nobody: Gmail pushes nothing"


async def test_a_scenario_without_the_email_writes_nothing_of_it(tmp_path: Path) -> None:
    _, store = await _run(tmp_path, Scenario.model_validate(BASE))
    assert _landed(store.events()) == []


async def test_an_email_to_nobody_with_a_mailbox_here_is_refused(tmp_path: Path) -> None:
    lost = {**EMAIL, "to": ["someone@nowhere.example"]}
    with pytest.raises((RunRefused, ValueError), match="nobody with a mailbox"):
        await _run(tmp_path, Scenario.model_validate({**BASE, "happenings": [lost]}))


async def test_a_fork_sends_an_email_after_its_checkpoint_and_the_parent_never_had_it(tmp_path: Path) -> None:
    scenario = Scenario.model_validate(BASE)
    parent, store = await _run(tmp_path, scenario)
    at_seq = min(seq for seq, c in checkpoints(store).items() if c.wake == 1)
    store.close()

    def open_parent(clock: Clock) -> SqliteStore:
        return SqliteStore(tmp_path / "world.db", "parent", clock)

    added = {"kind": "add_happening", "after": "PT1H", "happening": {k: v for k, v in EMAIL.items() if k != "after"}}
    await fork_run(
        fork=Fork.model_validate({"parent_run": "parent", "at_seq": at_seq, "overrides": [added]}),
        parent=parent,
        open_parent=open_parent,
        run_id="child",
        scenario=scenario,
        agent=AgentUnderTest(name="agent", sandbox=SANDBOX),
        sandbox=_Kernel(scenario),
        playback_for=lambda _store, _checkpoint: _NothingToPlay(),
        services=Services(providers=[build()]),
        replier_for=lambda s, _pins: PeopleReplier(s, None, []),
        state_dir=tmp_path / "state",
        own=_Program(),
    )
    child = SqliteStore(tmp_path / "world.db", "child", RunClock(scenario.starts_at))
    [landed] = _landed(child.events())
    assert landed.sim_time == scenario.starts_at + timedelta(hours=1)
    again = SqliteStore(tmp_path / "world.db", "parent", RunClock(scenario.starts_at))
    assert _landed(again.events()) == []
