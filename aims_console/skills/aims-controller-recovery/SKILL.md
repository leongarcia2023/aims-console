---
name: aims-controller-recovery
description: Navigation loop, after every sampleLocating scan. Checks the Z-controller status and, when the controller is off (the tip lost the surface during the scan), repeats the approach, the scan and the check with 100 nm more tip lift until the controller is on. Use after step 2 of the navigation prompt, before retracting the tip.
---

# Controller-status recovery (navigation loop, step 3)

This is a prescribed rule of the AIMS navigation protocol, packaged as a skill so that it is
stated once, versioned, and applied the same way in every run. It does not decide anything
the protocol leaves open.

## When to run it

Immediately after a `sampleLocating` scan completes (step 2 of the navigation prompt) and
before `retract_tip` (step 4).

## Procedure

1. Call `check_controller_status`.
2. If the Z controller is **on**: the scan is valid. Say so in one line and continue with step 4
   of the prompt (retract the tip).
3. If the Z controller is **off**: the tip retracted during the scan (it touched debris, or the
   surface drifted away) and the scan cannot be trusted. Repeat steps 1 to 3 of the prompt with
   the tip lift increased by 100 nm:
   - `approach_and_lift` with `temperature="cold"`, `low_limit=-3e-6`, `high_limit=3e-6` and
     `tip_lift` equal to the previous lift plus `1e-7` m (the prompt starts at `5e-7`);
   - `scan` with `series_name="sampleLocating"`, `pixels=128`, `lines=40`; wait for completion;
   - `check_controller_status` again.
4. Repeat step 3 until the controller is on. Stop after **5** retries (tip lift 1.0 µm): retract
   the tip and report to the operator that the approach keeps losing the surface; do not move
   the stage on an invalid scan.

## What to report

One line per attempt: the tip lift used, the scan file written, and the controller status read.
The scan that passes the check is the one the rest of the loop uses (its id is the latest
`sampleLocating` id).

## Notes

- The retry cap (5) is the console's safeguard; the team's prompt states the rule without a
  cap. Change it here, not in the prompt.
- Never call tools in parallel; each call waits for the previous one to finish.
