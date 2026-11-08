---
name: aims-gp-recovery
description: Navigation loop, after every position prediction. When the particle filter's uncertainty in x or y exceeds the scan size (53 µm), runs GP regression on the latest sampleLocating scan and predicts the position again on the reconstructed image; the prediction with the smaller uncertainty is the one to move on. Use after step 6 of the navigation prompt.
---

# Uncertainty-triggered GP recovery (navigation loop, step 7)

This is a prescribed rule of the AIMS navigation protocol, packaged as a skill so that it is
stated once, versioned, and applied the same way in every run. The threshold is the human-set
value from the protocol; the skill applies it.

## When to run it

Immediately after `predict_position` on a navigation scan (step 6 of the navigation prompt).

## Rule

Read the uncertainty the particle filter reported, `(u_x, u_y)` in µm.

- If `u_x <= 53` **and** `u_y <= 53`: the prediction stands. Say so in one line and continue with
  step 8 of the prompt.
- If `u_x > 53` **or** `u_y > 53` (larger than the 53 µm scan frame): the prediction is flagged
  unreliable. Reconstruct the image and predict again:
  1. `run_gp_regression` with `sxm_path` = the `.sxm` file of the latest `sampleLocating` scan
     (the path is in the scan result's `File:` field) and `output_path` = the same path with
     `_GP` appended to the stem and a `.png` extension (for example
     `.../sampleLocating0003_GP.png`). Keep the defaults (`factor=4`, 256 × 256 output,
     channel `MIM-Im_AC-X`).
  2. `predict_position` with `image_path` = that `_GP.png` and `scan_size_um=53`.
  3. Compare the two predictions. The one with the smaller uncertainty (compare `max(u_x, u_y)`)
     is the prediction that step 8 (`calculate_movement_from_prediction`) must use.

## What to report

Both predictions side by side (position, uncertainty, confidence, distance) and which one is
carried forward. If the GP prediction is also above the threshold, say so; the loop still moves
on the smaller-uncertainty prediction, as the prompt prescribes.

## Notes

- Run GP regression once per scan; do not iterate it.
- Never call tools in parallel; each call waits for the previous one to finish.
