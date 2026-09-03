# Recoup — Architecture

The expanded version of the README's architecture section: what each component
is responsible for, what it is deliberately not responsible for, and which
production-grade system it is the buildathon-sized version of.

---

## 1. The shape of a decision

Every action the agent takes passes through the same five steps, in this order:

```
score  →  propose  →  GATE  →  transition  →  execute
```

The order is the safety property, not a style choice. Execution is handed a
`PolicyDecision`, never a `ProposedAction`, so there is no code path that can
send a message without a verdict attached. A component that wanted to skip the
gate would have to fabricate an approval object to do it.

| Step | Module | Decides |
|---|---|---|
| score | [`app/core/scorer.py`](../app/core/scorer.py) | What is this invoice worth acting on? |
| propose | [`app/core/agent.py`](../app/core/agent.py) | What is the next rung, and how hard do we push? |
| gate | [`app/core/policy.py`](../app/core/policy.py) | Is that permitted? |
| transition | [`app/core/escalation.py`](../app/core/escalation.py) | May this case move? |
| execute | [`app/services/`](../app/services/) | Send it, and log that we did |

Everything is recorded by [`app/core/audit.py`](../app/core/audit.py) whichever
way it goes. A blocked action leaves as much evidence as a sent one.

---

## 2. Layering: why the core has no database

`app/core/` operates on plain Pydantic snapshots
([`app/core/domain.py`](../app/core/domain.py)), not ORM rows. Two adapters feed
it: one from the synthetic generator, one from the database.

This exists for three reasons:

1. **The demo must run on a clean checkout.** `scripts/run_batch_demo.py` needs
   no Postgres, no API keys and no network. A demo that requires infrastructure
   is a demo that fails when it matters.
2. **The safety tests must be checkable.** Every guarantee below is asserted by
   a unit test that runs in milliseconds against in-memory objects.
3. **One scorer, one gate, two data sources.** The API path and the batch path
   converge on the same `CaseSnapshot`, so they cannot drift into enforcing
   different policies.

The snapshot is deliberately a *subset*. The generator's ground-truth fields
(`recovered`, `true_recovery_probability`, `eventual_payment_days`) have no
adapter into this layer, so the agent structurally cannot read the answer it is
being evaluated on.

---

## 3. Component responsibilities

### Prioritization scorer

`EV = P(recovery) × outstanding × urgency_weight − intervention_cost`

The mirror image of standard credit-risk practice. A lender computes Expected
Loss as `PD × EAD × LGD`; this runs the same decomposition the other way to get
an Expected *Recovery*. The reference walkthroughs for the credit-scoring side
of that parallel are
[allmeidaapedro/Lending-Club-Credit-Scoring](https://github.com/allmeidaapedro/Lending-Club-Credit-Scoring)
and [levist7/Credit_Risk_Modelling](https://github.com/levist7/Credit_Risk_Modelling)
— methodology references, not dependencies.

**It is rules-based and says so.** Every prediction is returned with
`fallback_used=True` and `resolved_by=RULES_BASED_SCORER`, so nothing
downstream — including the batch report — can mistake a hand-tuned logistic for
a fitted model. Its coefficients are deliberately *not* copied from the
generator's outcome model; a scorer hand-fitted to the exact data-generating
process would report an accuracy that means nothing.

`WAIT` exists because contacting a customer who was about to pay is a cost. The
batch report counts those as false interventions rather than dropping them.

### Policy engine — the gate

Built on [venmo/business-rules](https://github.com/venmo/business-rules).
`build_policy_rules()` compiles a `PolicyConfig` into condition/action data that
`run_all` evaluates. The rules are *data*: `GET /api/v1/policy` serves the
compiled set verbatim, so what the gate enforces and what the API says it
enforces cannot diverge.

Two invariants:

- **Blocks are absolute; adjustments only reduce.** The rule actions are
  `block` and `clamp_discount`. There is no action that approves, raises a
  ceiling, or sends anything. A rule's only powers are to refuse and to lower.
- **Every violation is reported, not just the first.** `stop_on_first_trigger`
  is off, because an audit record saying "blocked by the contact cap" when it
  was *also* an opt-out is a misleading record.

> **The production-grade version of this is [Open Policy Agent](https://github.com/open-policy-agent/opa).**
> CNCF-graduated, Rego rules, a separate decision service, decision logs as a
> first-class artifact. Architecturally it plays exactly the role this module
> plays. It is not deployed here because a second service in Go is not a
> buildathon-sized dependency — the *pattern* is identical either way, and the
> migration path is to replace `PolicyEngine.evaluate` with an OPA query.

### Escalation state machine

Built on [pytransitions/transitions](https://github.com/pytransitions/transitions).
`monitoring → reminded → escalated → human_handoff`, with `closed` reachable
from anywhere.

Three properties are enforced structurally rather than by convention:

- **`auto_transitions=False`.** Left at its default, `transitions` generates a
  `to_<state>()` method for every state and any code could jump a case straight
  to `closed`, skipping the ladder. Disabling it means the four declared
  triggers are the only way a case can move.
  [A test asserts `to_closed` does not exist.](../tests/test_escalation.py)
- **Guards, not post-hoc checks.** `contact_allowed` and `ladder_step_due` are
  `conditions` on the transitions. A blocked transition does not happen — the
  trigger returns `False` and the state is unchanged — rather than happening and
  being flagged afterwards. Both guards delegate to the policy engine, so the
  FSM and the outbound gate cannot disagree.
- **Logging is attached to the transition.** `log_transition` is a `before`
  callback, so a state change that reaches the ledger is not something a call
  site has to remember to do.

**The ladder decides which rung is next; the scorer only decides whether to
move at all.** Deriving the trigger from the urgency tier instead looks right
and is a bug: a high-value case scores `ESCALATE` and tries to fire `escalate`,
which is not legal out of `monitoring`, so the most urgent invoices in the book
would silently do nothing.

The machine is built with `send_event=True`. Without it, a callback reading
`self.trigger` gets the model's bound `trigger` method that `transitions`
installs, not the firing event's name, and every audit line would read
`transition:<bound method ...>`.

### Decision Trace — the audit ledger

Append-only and hash-chained: each entry's `entry_hash` is a SHA-256 over its
own content plus its predecessor's hash. Editing, reordering or deleting an
entry breaks every hash after it, and `DecisionLedger.verify()` reports the
first position that disagrees. An ordinary log table is not an immutable
record; it is a record nobody has edited *yet*.

> **A production system would use [pyeventsourcing/eventsourcing](https://github.com/pyeventsourcing/eventsourcing)**
> — proper aggregates, snapshotting, and replay, which the Policy Simulation
> Engine would get for free. The hash chain here is the buildathon-sized version
> of the same guarantee, not a claim to have outdone it. It was evaluated and
> deferred: the audit ledger is on the critical path for every other component,
> and swapping its persistence model mid-build was the higher risk.

### Promise-to-pay tracker

**A promise is evidence of intent, never evidence of payment.** A customer
saying "I'll pay Friday" moves the invoice to `PROMISED` and buys them quiet
until Friday. It does not move money. Only a signature-verified Razorpay webhook
sets `PAID`.

A broken promise escalates *one rung*, per the pre-agreed ladder — not a retry
loop. Superseded promises are kept rather than deleted, because "this customer
has rescheduled three times" is exactly the pattern a human reviewer needs.

### Reply understanding

Built on [567-labs/instructor](https://github.com/567-labs/instructor), which
owns the schema-bound LLM path: it prompts for the Pydantic model, validates the
response, and on failure re-prompts *with the specific validation errors*. That
last part is why it replaced the hand-rolled version — a static "please return
valid JSON" retry cannot tell the model which field it got wrong.

instructor is scoped to schema-bound calls only. Drafting reminder copy and
narrating a decision go through `generate()`/`generate_explanation()` untouched.

A failed structured call never raises out of the classifier: it degrades to
`IntentLabel.OTHER` with `fallback_used=True` and routes to a human. A
collections agent that crashes on a strangely worded reply is worse than one
that asks for help.

> **Phase 4 upgrade path: [huggingface/setfit](https://github.com/huggingface/setfit).**
> Fine-tunes a sentence-transformer from as few as 8–16 labelled examples per
> class — far less than the 800–1,500 a TF-IDF/SVM or DistilBERT baseline would
> need. `src/ml/reply/service.py` already treats the classifier as a rebindable
> binding, so this is a one-line swap when labelled data exists.

### Webhook receiver

The only writer of `PAID`. Three properties in order of how badly getting them
wrong would hurt: signature verification before anything else; idempotency on
`event_id` (Razorpay retries, and double-counting a payment corrupts every
recovery number); and acknowledging what we *stored* rather than what we
understood, so an unparseable payload does not turn our bug into an infinite
retry storm.

---

## 4. Feature engineering and train–serve parity

Today there is one shared path: `app/core/domain.py` builds the same
`CaseSnapshot` from generated data and from database rows, and the scorer reads
it identically in both. That is train–serve parity by construction, at this
scale.

> **The industry-standard version is [feast-dev/feast](https://github.com/feast-dev/feast)**
> — a CNCF-hosted feature store built to solve exactly the training-serving skew
> problem that one shared function solves here. It becomes worth the
> infrastructure when features are computed by more than one system, or when
> point-in-time correctness across many entities stops being something a single
> `as_of` field can express.

---

## 5. Synthetic data

`src/data/synthetic_generator.py` is the single generator for the whole project.
Its design rules — hidden archetypes, outcomes generated once, one seeded RNG,
and a deliberate irreducible noise term — are documented in that module's
docstring.

The property that matters for honest metrics: **outcomes are sampled
independently of anything the agent does.** `recovered` is the outcome under *no
intervention*. That is what makes the evaluation report's careful wording
necessary — see below.

> **[sdv-dev/SDV](https://github.com/sdv-dev/SDV) is the upgrade path**: learn
> the statistical structure from a controlled seed batch, then expand it into a
> larger, statistically richer one while keeping the leakage-free label
> guarantee. Not adopted because the hand-rolled archetype generator is already
> producing a believable, non-uniform distribution, and replacing working,
> tested data generation was the lowest-value use of remaining build time.

---

## 6. Honest metrics

`docs/evaluation_report.md` is generated by `scripts/run_batch_demo.py` and
committed. Read it with these four things in mind, all of which the report
itself repeats:

1. **The scorer is rules-based, not trained.** Probabilities are not calibrated.
2. **The agent did not *cause* the recoveries it is credited with.** Because
   outcomes are sampled independently of agent behaviour, the recovery rate is a
   **targeting** measure — did the agent act on the invoices that were going to
   be paid? — not a causal one. Measuring uplift needs a holdout arm this
   simulation does not have, so no uplift number is reported.
3. **False interventions are counted as a cost.** A contacted customer whose
   invoice would have been recovered anyway appears in that row. It is a large
   number and it is supposed to be.
4. **Compliance violations are counted from the ledger, not from intent.** The
   detector walks the trace in sequence and counts a contact only when the
   *standing* policy verdict for that invoice was a block. It reads the audit
   record rather than the orchestrator's own return values, so a bug in the
   orchestrator cannot suppress the number that would reveal it.

---

## 7. Roadmap

| Next step | Replaces | Why not now |
|---|---|---|
| [OPA](https://github.com/open-policy-agent/opa) | `PolicyEngine` | A second service in Go; the pattern is already correct |
| [Feast](https://github.com/feast-dev/feast) | shared `CaseSnapshot` | Real infrastructure; skew is not yet a live risk |
| [eventsourcing](https://github.com/pyeventsourcing/eventsourcing) | hash-chained ledger | On the critical path for everything else |
| [SetFit](https://github.com/huggingface/setfit) | LLM reply baseline | Needs labelled replies that do not exist yet |
| [SDV](https://github.com/sdv-dev/SDV) | archetype generator | Current generator works and is tested |
| [MABWiser](https://github.com/fidelity/mabwiser) | fixed contact cadence | Phase 11 stretch: contextual bandit for contact timing |

Plus the non-library work named in the README: multi-tenant auth, WhatsApp
delivery once verification clears, a trained and calibrated recovery model with
proper held-out evaluation, secrets management and observability, and a real
compliance review of the escalation cadence before any live customer is
contacted.

[invoiceninja/invoiceninja](https://github.com/invoiceninja/invoiceninja) is
worth ten minutes as UX prior art for reminder scheduling — source-available,
PHP/Laravel, not a dependency candidate.
