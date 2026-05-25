# Guideline Proposals

Proposed corrections and additions to `docs/stateflow_model_creation_guideline.md`,
derived from reviewing `docs/some_proposal.md` against the reference example
`example/model_gen/Ex1_StMach_sf.yaml` and the existing YAML schema.

---

## 1. Correction — Section 4.5: `count()` prohibition is too broad

**Current text (wrong):**
> Do not use `count()` to track how long a boolean condition has been true. Use `duration()` instead.

**Problem:**  
`count(cond)` and `duration(cond)` are not interchangeable. `count()` counts consecutive
clock ticks that a condition is true; `duration()` returns elapsed seconds. For a discrete
system where the timeout parameter is specified in ticks (e.g., `startupTout: uint8`),
`count()` is the correct operator. The prohibition was inferred from a specific misuse
in a chat example, not from a general rule.

The reference YAML uses `count()` correctly throughout:
```matlab
count(devStatus ~= DevStatus_e.STANDBY) > startupTout   % startupTout in ticks
count(~linkOk) > faultTout                               % faultTout in ticks
```

**Proposed replacement:**

> Choose between `count()` and `duration()` based on the unit of the timeout parameter.
>
> | Operator | Measures | Use when timeout is |
> | -------- | -------- | ------------------- |
> | `count(cond)` | clock ticks that `cond` is true | integer tick count (e.g., `uint8`) |
> | `duration(cond)` | seconds that `cond` is true | real-time value (e.g., `single`, seconds) |
>
> Mixing them (using `count()` with a seconds-valued threshold, or `duration()` with a
> tick-valued threshold) is the actual error to avoid.

---

## 2. Addition — Section 4: Transition conflict resolution rule

**Gap identified by `some_proposal.md` section 2.3 — valid.**

The guideline says transitions must be "mutually exclusive (preferred)" but gives no rule
for simultaneous valid guards.

**Proposed new subsection 4.9:**

The YAML schema already carries the resolution mechanism: every transition has an explicit
`order:` field. Lower numeric value = higher priority = checked first. If a lower-priority
transition guard is also true, it is skipped in favour of the higher-priority one.

```yaml
- from: ACTIVE.STARTUP.READY
  to: ACTIVE.STARTUP.FAULT_ACTIVE
  condition: count(~linkOk) > faultTout
  action: dev_fault=DevFault_e.FAULT_LINK_LOST
  order: '1'                               # checked first

- from: ACTIVE.STARTUP.READY
  to: ACTIVE.STARTUP.FAULT_ACTIVE
  condition: count(devErr) > faultTout
  action: dev_fault=DevFault_e.FAULT_DEV_ERR
  order: '2'                               # checked only if order-1 guard is false
```

Rules for the guideline:
- Every transition must carry an explicit `order` value. Omitting it is an error.
- Within a state, transitions are evaluated in ascending `order` (1 is highest priority).
- The first transition whose guard evaluates to true is taken; remaining candidates are
  not evaluated.
- Two transitions with the same `order` value from the same source state are a modeling
  error — the generator should reject them.

---

## 3. Addition — Section 9: Variable initialization contract

**Gap identified by `some_proposal.md` section 3.6 — valid.**

The guideline says "no implicit state" but does not require variables to declare their
initial value. This matters most for locals that are read before being written on the
first execution, and for fault flags that must default to a safe value.

**Proposed addition to section 9:**

Every local variable must define:
- an **initial value** (declared in the YAML `locals:` block or explicitly set in the
  top-level default state's `entry:` action)
- a **reset owner** — which state (and which action hook) is responsible for resetting it

The reference pattern from the YAML:
```yaml
locals:
- name: hasFault
  type: boolean    # initial value: false (bool default), reset in STARTUP/STANDBY entry
```

```yaml
STARTUP:
  en: hasFault=false;   # reset owner: STARTUP entry — explicit, not left to default

FAULT_ACTIVE:
  en: hasFault=true;    # setter: FAULT_ACTIVE entry
```

The parent transition condition `condition: hasFault` is safe because `hasFault` is
always reset before it can be read (subchart entry clears it before substates run).

---

## 4. Addition — Section 17: Hierarchical fault flag pattern

A third fault output pattern worth documenting alongside Pattern A and B.

**Pattern C — Fault flag propagation (subchart boundary)**

Used when fault detection happens inside a subchart but the routing decision must be
made at the parent level. A local boolean (`hasFault`) acts as the
subchart-to-parent signal.

```yaml
# Subchart entry: reset flag
STARTUP:
  en: hasFault=false;

# Fault state inside subchart: set flag
FAULT_ACTIVE:
  en: hasFault=true;

# Fault output assigned at detection point (Pattern A, inside subchart)
- from: STARTUP.INIT
  to: STARTUP.FAULT_ACTIVE
  condition: count(~devOnline) > startupTout
  action: dev_fault=DevFault_e.FAULT_LINK_TOUT

# Parent checks flag, routes to top-level error handling
- from: ACTIVE.STARTUP
  to: ACTIVE.ERROR
  condition: hasFault
```

Properties:
- `dev_fault` is still assigned at the detection point (Pattern A — causal, no stale risk).
- `hasFault` only propagates the fact that any fault occurred; it does not carry the fault
  code. The parent does not need to know which fault fired.
- The subchart resets `hasFault` on entry, so re-entry always starts clean.
- Safe to add new fault transitions inside the subchart without changing parent logic.

When to use:
- Detection state is deep inside a subchart.
- Parent only needs to know "did a fault occur", not which one.
- Multiple inner fault transitions would otherwise require the parent to enumerate all
  inner states.

---

## 5. On `some_proposal.md` — disposition

| Section | Assessment | Action |
| ------- | ---------- | ------ |
| 1. Strengths | Accurate summary | No action |
| 2.1 Missing execution semantics | Valid — partially addressed by item 2 above | Absorbed |
| 2.2 Predicate lifecycle | Already covered by guideline section 16 | No action |
| 2.3 Transition priority undefined | Valid — addressed by item 2 above | Absorbed |
| 2.4 DSL schema too shallow | Out of scope for the guideline | Defer to YAML schema spec if built |
| 2.5 Layout not machine-enforceable | Misidentifies scope; layout is `slxgen`'s job | Reject as guideline concern |
| 2.6 Temporal split undefined | Already covered by guideline section 6.3 | No action |
| 2.7 Fault context safety gap | Already covered by guideline section 17 | No action |
| 3.1–3.5 Improvements | Partially absorbed above | See items 1–4 |
| 3.6 Initialization contract | Valid — addressed by item 3 above | Absorbed |
| 3.7 Replace layout rules | Same as 2.5 — wrong scope | Reject |
| 4. FSM Rule Engine | Reference interpreter spec, not a guideline. Also has a bug: Phase 2 evaluates predicates before Phase 3 updates timers, but predicates depend on timer values. | Do not merge into guideline; if pursued, fix the phase ordering and put in a separate implementation spec. |
| 5. Final recommendation | Aspirational; relevant only if building a formal SIR | No action now |

Layout concerns from sections 2.5 / 3.7 are already tracked in `work/proposals.md`.
