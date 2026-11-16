# CHANGES-OURS

What this fork (`ours`, from upstream v0.3.14 `85c3961`) does differently from upstream. One entry per change:
what it is, why, the files, and its tests.

## 1. Playback answers the agent's model calls from the record

**What.** A fork brings its agent back by playing its recorded inputs into a fresh sandbox up to the checkpoint.
Upstream answers the agent's *service* calls from the record during that playback, but loads only the model calls
made *after* the checkpoint (`session.fork`: `spans(after_wake=forked_after)`), so a model call the agent makes
while being played back finds no recorded answer and goes to the model. A model asked again words its answer
differently, the agent's next service call (the mail it sends) then differs from the record's, and the fork is
refused: "the fork's agent did not do what it did before the checkpoint". With a deterministic stand-in model
this never shows; with a real model (measured: 10 identical requests at temperature 0, 9 to 10 different
answers) every fork of an LLM agent was refused (6 of 6).

Here the model calls placed in the wakes up to the checkpoint are loaded too, as a second, frozen `ModelReplay`
(`ProxyAddon.playback_model_replay`), which answers model calls while a playback is in progress (`play_back` to
`live`) and is dropped when the checkpoint is reached. A call it does not hold is refused, never sent on.

**Files.** `ports/store.py`, `adapters/store/sqlite.py`, `application/cases.py` (`spans(until_wake=)`),
`session.py` (`fork`), `adapters/proxy/addon.py` (`playback_model_replay`, `_replayed`, `live`).

**Tests.** `tests/proxy/test_playback_model_calls.py`, `tests/test_store_spans.py`
(`test_spans_are_read_up_to_a_wake_by_the_store`). Mutations tried: answering from `model_replay` during a
playback (the playback test fails: the model's new words reach the agent), and `wake<` for `wake<=` (the store
test fails).

**Cost.** The parent's model calls up to the checkpoint are held in memory during the playback and released at
the checkpoint: a later fork point holds more.

## 2. A fork may make every model call again (`Fork.live`, `minutehand fork --live`)

**What.** Upstream answers a fork's model calls after the checkpoint from the parent's record until the agent asks
something the parent did not, and a person's words from any run in the same world file that asked the same
(`Store.written`). So a fork with no change copies its parent word for word, whatever its seed: measured, 3 control
forks of one checkpoint were identical. An attack fork changes what the agent is asked, parts at once and calls the
model live. The two lines then differ in more than the attack. With `live: true` (or `--live`) the fork holds no
recorded model answer after the checkpoint (`ModelReplay` built from nothing), and its store answers `written` None
(`SqliteStore.fork(afresh=True)`), so every model call, the agent's, the people's and the judges', is made again.
Up to the checkpoint nothing changes: the playback still answers from the record (change 1). `live` and `--frozen`
together are refused.

**Files.** `domain/experiment.py` (`Fork.live`), `ports/store.py`, `adapters/store/sqlite.py`, `application/cases.py`
(`fork(afresh=)`), `application/rewind.py`, `session.py`, `cli.py` (`--live`).

**Tests.** `tests/orchestrator/test_fork_adds_a_happening.py::test_a_fork_made_afresh_is_answered_nothing_from_the_file_and_one_not_is`.
Mutation tried: `written` ignoring `afresh` (fails).

## 3. A fork may add a happening (`add_happening`)

**What.** Upstream's overrides change what is (a person's behaviour, a memory, a deadline); none adds something
that happens. `AddHappening` adds any `Happening` a scenario may hold (a Slack post, a document edited, a ticket
moved), `after` the checkpoint's moment. It is put at the end of the scenario's `happenings`, so every happening the
parent scheduled keeps its index (a checkpoint's `PendingHappening` names one by index), its `after` rewritten from
the scenario's start, and booked as pending at the checkpoint. The happening is written without its own `after` or
`gap`; one that has either is refused, and so is a person not in the scenario.

```yaml
overrides:
  - kind: add_happening
    after: PT30M
    happening: {kind: posts, provider: slack, person: sam, text: "..."}
```

**Files.** `domain/experiment.py` (`AddHappening`), `application/rewind.py` (`changed_scenario(forked_at=)`,
`_with_added`), `session.py` (`_forked_at`).

**Tests.** `tests/orchestrator/test_fork_adds_a_happening.py`: fires its `after` past the checkpoint; the parent's own
happening keeps its moment; the log up to the checkpoint is the parent's event for event and the parent is not
written to; no added happening plays as before; several fire each at its moment; refusals. Mutations tried: due at
the checkpoint itself (2 fail), added first instead of last (3 fail). Found in a real run and fixed: a fork's
`fork.json` keeps the happening with the override's `after` filled in, and reading it back for the report refused
it (`test_a_fork_with_an_added_happening_reads_back_as_it_was_kept`); the run itself was kept, only its report failed.

## 4. A person sends an email by themselves (`sends_email`)

**What.** Upstream's Gmail holds only the emails a scenario seeds before the start and the replies people write to
the agent; no happening family sends one. `PersonEmails` (`kind: sends_email`) is a new member of `Happening`: a
person's email at a moment, built as a mail client builds one (`From` the person's name and their own address or
`from_address`, so an outside person may write from a look-alike domain), delivered unread to the inbox of each
recipient (the scenario's owner when `to` is empty) through a new port, `ports.provider.SendsEmail`, which Google
Workspace implements (`send_email`, through `MailWorld.deliver`, actor PERSON). It **wakes nobody**: Gmail tells the
agent of no new mail (`users.watch` is not served), so it is among the happenings `Orchestrator._unheard` lists, and
the agent finds it on its next poll. A run whose email lands on a provider without the port, or to nobody with a
mailbox, is refused by name. In a fork it is added with `add_happening` (change 3).

```yaml
happenings:
  - {kind: sends_email, provider: google_workspace, person: kevin, after: P1DT2H,
     from_address: kevin@partner-mai1.example, subject: Q3 numbers, text: "..."}
```

**Files.** `domain/scenario.py` (`PersonEmails`), `ports/provider.py` (`SendsEmail`),
`adapters/providers/google_workspace/provider.py` (`send_email`), `application/orchestrator.py` (`_happen`, `_mailer`,
`_unheard`, `_refuse_unlanded_happenings`), `application/standing.py`, `application/forks.py` (report wording),
`schemas/scenario.schema.json`, `schemas/seed.schema.json` (regenerated).

**Tests.** `tests/providers/google_workspace/test_person_sends_email.py`: lands unread in the owner's inbox at its
moment as the person's, from the given address, and wakes nobody; a scenario without it writes nothing; one to nobody
with a mailbox is refused; a fork adds one after its checkpoint and the parent never had it. Mutations tried: landing
read instead of unread (1 fails), the orchestrator ignoring it (3 fail), and an email waking the agent (1 failed
before `_unheard` listed it: that is how the wake was found).

## 5. A played-back agent reads back what it just wrote

**What.** While a fork's agent is played back to its checkpoint, the receiver answers its memory as the parent's log
stood at a cursor, `TraceReplay.reached_seq`: the last seq of the calls answered from the record. A write of the
agent's memory is no such call, so it moves no cursor, and a read right after it in the same wake was answered as
the log stood before it. An agent that notes something and reads its notes back in one wake (ours: `remember`, then
the next model call's prompt lists the notes) then sends its model another prompt than the record's; the frozen
playback refuses that call, the agent sends one mail fewer, and the fork is refused ("sent nothing where the record
holds a call"). Every fork of our 30-day run was refused this way; a week-long one never was, its agent never wrote
and read back in one wake. Upstream's `_Playback` answered a write as it would have landed and kept it nowhere; ours
keeps it in the memory it answers from (`_memory`, `_versions`) until the cursor moves and the log is read again,
which by then holds the parent's own write. Nothing reaches the log either way.

**Files.** `adapters/telemetry/receiver.py` (`_Playback.answer`).

**Tests.** `tests/test_playback_reads_its_own_writes.py`: a write then a list in the same position reads it back; a
delete is gone on the next get; once the cursor passes, the log answers and nothing was written. Mutation tried:
upstream's behaviour, the write kept nowhere (2 fail).

## 6. Minutehand's own model calls may wait longer (`MINUTEHAND_MODEL_TIMEOUT`)

**What.** Minutehand's own model (the people, the judged checks, the reviewer) is called through a client with a
fixed 120 s timeout. Judging a month-long run on a self-hosted model took longer, and both judged checks of our
30-day run were blocked ("the model failed: … Request timed out"). `MINUTEHAND_MODEL_TIMEOUT` (seconds) sets it;
unset, 120 s as upstream; not a positive number, refused by name.

**Files.** `adapters/model/pydantic_ai.py` (`timeout_seconds`). **Tests.** `tests/test_playback_reads_its_own_writes.py`
(the variable read and refused), `tests/model/test_pydantic_ai.py::test_the_model_timeout_reaches_every_request_the_providers_own_client_makes`
(end to end: a provider answering after 2 s fails at 0.5 s and answers at 8 s; mutation tried: the variable ignored, 1 fails).
A longer timeout alone did not unblock the reviewer: its calls were not slow, they never stopped (change 7).

## 7. Minutehand's own model answers may be capped (`MINUTEHAND_MODEL_MAX_TOKENS`)

**What.** Sampling the self-hosted model's own counters while the shared reviewer judged our 30-day run showed one
request generating for over 20 minutes and 50,000 tokens without ending: a reasoning model that on some prompts does
not stop. With no cap the call lasts as long as any timeout allows. `MINUTEHAND_MODEL_MAX_TOKENS` sends a
`max_tokens` with every call of Minutehand's own model (people, judged checks, reviewer); unset, none is sent, as
upstream; not a whole number above 0, refused by name.

**Files.** `adapters/model/pydantic_ai.py` (`max_tokens`, `PydanticAIModel.answer`). **Tests.**
`tests/model/test_pydantic_ai.py::test_the_model_max_tokens_caps_each_answer_and_unset_sends_none`, `test_a_bad_model_max_tokens_is_refused`.
Mutation tried: the cap not sent (1 fails).
