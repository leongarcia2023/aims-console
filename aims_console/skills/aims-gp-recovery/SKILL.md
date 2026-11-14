---
name: aims-gp-recovery
description: Read this when a position estimate from predict_position is uncertain, for example when its uncertainty in x or y is larger than the 53 µm scan frame, its confidence is low, or the scan shows few markers (noise, poor contrast, a nearly empty frame). It describes the usual causes and the remedies, chiefly reconstructing the scan with GP regression and estimating again, keeping the estimate with the smaller uncertainty.
---

# Uncertain position estimate: a scan of poor quality

This is laboratory experience with cryogenic scanning-probe navigation, written down so that
it is the same in every run: what the failure looks like, why it matters, what fixes it.

## How it shows

`predict_position` returns the particle filter's estimate with a one-sigma uncertainty
`(u_x, u_y)` in µm and a confidence. An uncertainty larger than the scan frame (53 µm) in x or
y means the filter could not match the scan to one place on the template: the estimate is not
reliable, whatever distance it reports. Usual causes:

- the scan was interrupted by a tip retraction (see aims-controller-recovery; check the
  controller status before anything else, because then the remedy is a new scan);
- poor contrast or noise in the image;
- few markers in the frame. On the marker chip this happens near the target: there is no marker
  within about 42 µm of the template origin, so a 53 µm frame there can be nearly empty.

## Why it matters

A stage move on an unreliable estimate can take the tip away from the target, and an
unreliable estimate that happens to report a small distance is no evidence that the tip has
arrived. Check it before stopping on it.

## Remedies

1. If the controller was off during the scan, take a new scan (aims-controller-recovery).
2. Otherwise reconstruct the image with GP regression and estimate again:
   - `run_gp_regression` with `sxm_path` = the `.sxm` file of the latest `sampleLocating` scan
     (the scan result gives its path) and `output_path` = the same path with `_GP` added to the
     stem and a `.png` extension (for example `.../sampleLocating0003_GP.png`). Keep the
     defaults (`factor=4`, 256 × 256 output, channel `MIM-Im_AC-X`). It takes several minutes
     and reports nothing meanwhile; wait for it.
   - `predict_position` with `image_path` = that `_GP.png` and `scan_size_um=53`.
   - Keep the estimate with the smaller uncertainty (compare `max(u_x, u_y)`).
3. If the kept estimate is still above 53 µm, the position remains uncertain. Say so. Moving on
   the smaller-uncertainty estimate and scanning again at the new place gives a fresh estimate.
   Run GP regression once per scan; do not iterate it.

## What to report

Both estimates side by side (position, uncertainty, confidence, distance) and which one you
keep. Call one tool at a time and wait for each to finish.
