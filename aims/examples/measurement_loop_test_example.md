# Measurement Loop

> **DO NOT run any tools in parallel. DO NOT stop the experiment in middle!**
> Run all the tools following the order of the prompt:

1. Use the data in `examples\VbgScans` and `analyze_sxm_dynamics` tool to find a best measurement region with size 2x0.5um.
2. Use the `analyze_twist_angle_disorder` tool, load the 3x12 grid data from folder `examples\GridSpectra`, and use sxm image `examples\VbgScans\Scan0001.sxm`, average every four files. The device does not have top gate and only has bottom gate. The bottom hBN thickness is 15nm, and target material is MoSe2. Show the twist angle distribution and your physical interpretation.
3. Can you recommend three good measurement positions to me based on the grid spectroscopy GWC scores?
