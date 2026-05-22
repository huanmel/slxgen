```markdown
# Stateflow Design Guidelines (Engineering + Verification + Automation Ready)

## 0. Purpose

This document defines guidelines for designing Stateflow state machines that are:

- consistent and readable
- maintainable over long lifecycle
- suitable for testing and debugging
- traceable to requirements
- optionally compatible with formal verification
- suitable for automation (e.g., generation from text / LLM / DSL)

The key idea is:

> One modeling style is not enough — choose a **design level**, but keep a **common structural backbone**.

---

# 1. Design Philosophy

## 1.1 State machines are not logic containers

A state machine should represent:

- system **modes**
- not detailed algorithmic behavior
- not procedural sequences

If logic dominates structure → model is incorrect.

---

## 1.2 Explicitness over cleverness

Prefer:

- explicit states
- explicit transitions
- explicit predicates

Avoid:

- hidden logic in transitions
- implicit dependencies
- overloaded conditions

---

## 1.3 Separation of concerns

Split model into:

- **FSM (control logic / modes)**
- **predicate layer (conditions)**
- **time layer (timing logic)**
- **data layer (signals/state variables)**

---

# 2. Modeling Levels (choose intentionally)

## Level 0 — Rapid / readable model (default)

Focus:
- readability
- fast development
- debugging

Characteristics:
- moderate use of temporal operators
- simple hierarchy
- minimal abstraction layers

Use when:
- prototype
- internal tools
- non-critical systems

---

## Level 1 — Industrial / maintainable model (recommended default)

Focus:
- long-term maintainability
- testability
- traceability

Rules:
- predicates used for complex conditions
- limited transition actions
- structured timers (mix of `after()` and variables)
- shallow hierarchy

Use when:
- production control systems
- team development

---

## Level 2 — Verification-ready model

Focus:
- formal analysis compatibility
- deterministic structure
- bounded behavior

Rules:
- no side effects in transitions
- explicit state encoding
- bounded variables
- minimal AND decomposition
- no ambiguous guards

Use when:
- safety-critical systems
- verification required

---

## Level 3 — Automation-ready model (LLM / DSL generation)

Focus:
- machine generation
- model transformation
- reproducibility

Rules:
- strict structure (JSON/YAML-compatible)
- no hidden state
- standardized predicates
- explicit timers
- minimal free-form logic

Use when:
- generating Stateflow from text or models

---

# 3. State Design Rules

## 3.1 States must represent stable modes

A state must be:

- observable in system behavior
- stable over time
- semantically meaningful

❌ Bad:
- “check_sensor”
- “increment_counter”
- “wait_5ms”

✔ Good:
- “IDLE”
- “RUNNING”
- “FAULT”
- “STABILIZING”

---

## 3.2 Avoid micro-states

Do NOT encode:
- steps of an algorithm
- short computations
- temporary conditions

---

## 3.3 State naming convention

Use:

- UPPER_CASE for modes
- verb-like names only for actions outside FSM

Example:
- `IDLE`
- `ACTIVE`
- `FAULT_RECOVERY`

---

# 4. Transition Design Rules

## 4.1 Transitions must be deterministic

Each transition must:

- have clear condition
- be mutually exclusive with others (preferred)
- avoid overlap

---

## 4.2 No side effects in transitions (recommended)

❌ Avoid:
- resetting variables
- triggering actions
- modifying system state

✔ Prefer:
- entry/exit actions in states

---

## 4.3 Transition conditions must be simple

Preferred forms:

- boolean flags
- predicates
- simple comparisons

Avoid:
- complex multi-line expressions
- embedded algorithmic logic


## 4.4 Predicate abstraction rule

If condition exceeds complexity threshold:

> move it into named predicate

Example:


isSafeEntry
isSystemStable
isFaultResolved



# 5. State Actions Rules

## 5.1 Entry actions

Use for:
- initialization
- reset logic
- state activation setup

---

## 5.2 During actions

Use for:
- continuous control logic
- monitoring
- updates

---

## 5.3 Exit actions

Use for:
- cleanup
- finalization
- logging

---

## 5.4 Avoid mixing responsibilities

Do not:
- compute transitions inside actions
- modify unrelated subsystem state

---

# 6. Temporal Logic Guidelines

Stateflow supports:

- `after()`
- `duration()`
- `count()`

## 6.1 Acceptable usage

Use temporal operators when:

- logic is local to a state
- timing is short-term
- behavior is not safety-critical alone

Example:
```

after(2, sec)
duration(sensor_ok, 5, sec)



## 6.2 When NOT to use temporal operators

Avoid when:

- timing is part of safety decision
- timing must be observable externally
- timing is reused across states

---

## 6.3 Hybrid rule (recommended)

Critical timing logic should be:

- optionally duplicated as explicit signal
- or exposed for observability

---

# 7. Timer Design Rules

## 7.1 Two acceptable timer styles

### Style A — Native Stateflow timers
- `after()`, `duration()`, `count()`
- compact and efficient

### Style B — Explicit timers (state variables)
- `timer += dt`
- fully observable and testable

---

## 7.2 Selection guideline

| Use case | Preferred |
|----------|----------|
| local FSM logic | Stateflow timers |
| safety / diagnostics | explicit timers |
| formal verification | explicit timers |
| automation pipelines | explicit timers |

---

# 8. Hierarchy Rules

## 8.1 Keep hierarchy shallow

Recommended:
- max 2–3 levels

---

## 8.2 OR decomposition (default)

Use when:
- modes are mutually exclusive

---

## 8.3 AND decomposition (restricted)

Use only when:
- subsystems are truly independent

Avoid:
- coupling orthogonal regions

---

# 9. History Junction Rules

## 9.1 Use sparingly

Use only when:

- requirement explicitly demands resume behavior

---

## 9.2 Avoid when:

- behavior must be deterministic and analyzable
- system is safety-critical

---

# 10. Data Handling Rules

## 10.1 Avoid implicit state

All memory must be explicit via:

- states
- variables
- timers
- history (controlled use only)

---

## 10.2 Bound all variables

- saturate counters
- avoid unbounded growth
- avoid unconstrained floats in FSM logic

---

# 11. Testing & Debuggability

## 11.1 Each state must be testable

Define:

- entry condition
- exit condition
- expected invariants

---

## 11.2 Transition coverage

Ensure:

- all transitions are reachable
- no dead states exist
- no hidden transitions

---

# 12. Requirements Traceability

Each element should map to requirement IDs:

- States → system modes requirements
- Transitions → behavior requirements
- Predicates → condition requirements
- Timers → timing requirements

Example:
```

RUNNING [REQ-12]
IDLE -> RUNNING [REQ-15]
isSafeEntry [REQ-22]

````

---

# 13. Automation / LLM Compatibility Rules

To enable FSM generation from text:

## 13.1 Use structured abstraction

Preferred intermediate format:

```json
{
  "states": [],
  "transitions": [],
  "predicates": [],
  "timers": []
}
````

---

## 13.2 No free-form logic in transitions

All conditions must reference:

* predicates
* simple signals
* timer outputs

---

## 13.3 Deterministic structure required

* no ambiguous transitions
* no implicit priorities
* no hidden state

---

# 14. Formal Verification Compatibility (optional layer)

If required:

## 14.1 Must be true:

* finite state space
* deterministic transitions
* bounded variables
* no side effects in guards

---

## 14.2 Prefer:

* explicit timers
* shallow hierarchy
* no history junctions
* minimal AND decomposition

---

# 15. Recommended Default Architecture

Most robust pattern:

### Layer 1: FSM (Stateflow)

* system modes only

### Layer 2: Predicates (Simulink / functions)

* semantic conditions

### Layer 3: Timing layer

* hybrid (Stateflow + optional explicit signals)

### Layer 4: Monitoring / safety supervisor

* optional second FSM

---

# 16. Final Principle

> A good Stateflow model is not the one that is most compact,
> but the one that is **most predictable, testable, and transformable into other representations**.

---

# End of Guidelines

```

If you want, next step I can extend this into:

- :contentReference[oaicite:0]{index=0}
- or :contentReference[oaicite:1]{index=1}
- or :contentReference[oaicite:2]{index=2}

All three are directly aligned with your automation goal.
```
