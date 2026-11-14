---
name: aims-controller-recovery
description: Read this when a sampleLocating scan may have been taken with the tip out of contact, for example when the Z controller reads off after a scan, or when the image has rows of noise or flat, featureless bands. It describes how a tip retraction during a scan shows in the tool outputs, why such a scan misleads the position estimate, and how to recover (approach again with more tip lift and scan again).
---

# Interrupted scan: the tip retracted during a locating scan

This is laboratory experience with cryogenic scanning-probe navigation, written down so that
it is the same in every run: what the failure looks like, why it matters, what fixes it.

## How it shows

- `check_controller_status`, called after a scan, reports the Z controller **off**. The tip lost
  the surface during the scan (it touched debris, or the surface drifted away), and the
  feedback switched off. This is the direct test; the controller reads on after a good scan.
- The scan image (`get_sxm_image`, `convert_sxm_to_png`) has horizontal bands: rows of noise,
  or rows with no contrast, often from some line of the scan onward.
- A position estimate from such a scan often has a large uncertainty and a low confidence,
  because part of the marker pattern is missing. A low uncertainty does not prove the scan is
  good: check the controller.

## Why it matters

A scan taken while the tip was out of contact does not show the sample where the scan says.
A position estimated from it can be far from the truth, and a stage move on that estimate takes
the tip the wrong way.

## Remedy

1. Do not estimate the position from that scan.
2. Approach again with `approach_and_lift` (`temperature="cold"`, `low_limit=-3e-6`,
   `high_limit=3e-6`) and a tip lift 100 nm larger than the last approach (the usual first lift
   is 500 nm, `tip_lift=5e-7`). More lift makes another retraction less likely.
3. Scan again with the same settings (`series_name="sampleLocating"`, `pixels=128`,
   `lines=40`), wait for it to finish, and call `check_controller_status` again.
4. If the controller is off again, repeat with another 100 nm, up to five times (1.0 µm of
   lift). If it is still off then, retract the tip and report to the operator that the approach
   keeps losing the surface; do not move the stage on an invalid scan.
5. When the controller reads on after the scan, the scan is valid: retract the tip and use it.
   Its id is the latest `sampleLocating` id.

## What to report

One line per attempt: the tip lift, the scan file and the controller status. Call one tool at a
time and wait for each to finish.
