# Evals

`tests/tempo_logger_tests.py` tests the scripts. These test the other half: whether a model reading `SKILL.md` actually does what it says. Nothing in the scripts can check that, and it is where most of `SKILL.md`'s words go.

Each eval is one realistic prompt plus the things that must be true of the run. The expectations are about behaviour, not about hours — the timesheet these run against keeps changing, and an eval pinned to a number would start failing next week for a reason that has nothing to do with the skill.

## Read path first

Evals 1 to 6 end in a report. None of them may write, so a run that goes wrong costs tokens and nothing else. That is also why these came first: the rules with the most at stake on the read path — say which span you are reporting, never read an empty result as a failed one, never invent an expected figure — are exactly the ones no script can check.

Evals 7 to 10 are on the write path, and each of them is correct only if nothing was written, because the answer to a write request is a table and a pause. Each needs a snapshot of every worklog taken before the run and a sweep afterwards, and that snapshot is the instrument: the timesheet must come out byte-identical to it. No harness that takes it ships with the plugin.

## Placeholders

Two values in the prompts are filled in before a run rather than written into the file. `<DATE>` becomes a sandbox date of the run's own, on a part of the calendar that holds nothing. `<ITEM>` becomes the Jira key of a work item from the runner's own catalogue, because the work items a prompt can name are the ones in that person's own Tempo history.

## Running them

Requires a working token, the same one `setup check` uses. **Run only the read-path evals (1-6) this way.** Evals 7-10 exercise writes, and their correct outcome is that nothing was written; without a step that snapshots the timesheet first, gives each run its own date and sweeps afterwards, the baseline arm can write real worklogs. No such harness ships with the plugin.

The runner is `skill-creator`: it spawns one subagent per eval with the skill loaded and one without, grades each run against the expectations, and aggregates the two into a benchmark. The baseline matters — it is what says whether the skill is doing anything a model would not have done anyway.

```
/skill-creator run the evals for tempo-logger
```

Results land in a workspace beside the skill directory, one directory per iteration, and are not committed.
