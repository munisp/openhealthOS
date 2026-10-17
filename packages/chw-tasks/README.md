# chw-tasks

Community health worker task engine — spec module 3, OpenSRP pattern.

A CHW's day is a sorted work list computed from household census data:

- **ANC contacts** — WHO 2016 eight-contact schedule (Nigeria FMoH adopted),
  anchored to LMP. Long-past contacts are skipped; the next contact is
  always generated because a missed contact is itself a risk signal.
- **Immunisation** — Nigeria NPI routine schedule (birth dose through
  second measles). Doses drop off the work list after a 60-day catch-up
  window; catch-up beyond that is a facility decision, not a doorstep task.
- **Danger-sign referrals** — a small, high-specificity red-flag set.
  Unknown signs are rejected (a typo must not downgrade an emergency).
  Immediate signs are priority 0, due the day observed.
- **Escalation sweep** — overdue danger signs escalate to the facility
  nurse-in-charge and are never quietly expired; overdue ANC/immunisation
  tasks escalate to the CHW supervisor, then expire after their window.

The engine is pure and deterministic — same census, same tasks, on any
device, with no wall-clock reads inside the generators. That is what makes
offline-first safe here: two CHWs' devices holding the same census record
generate identical work lists and reconcile trivially on sync.

## Usage

```python
from datetime import date
from chw_tasks import Household, Member, TaskEngine

engine = TaskEngine()
engine.register_household(household, today=date.today())
engine.raise_danger_sign("HH-100", "M-02", "convulsions", date.today())
for task in engine.work_list(date.today()):
    print(task.priority, task.due, task.label)
```

## Tests

```bash
python3 -m unittest -v
```
