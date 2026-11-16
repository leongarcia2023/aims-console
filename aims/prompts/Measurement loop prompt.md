# Measurement loop prompt

DO NOT run any tools in parallel. DO NOT stop until you finish the last step!
Run all the tools following the order of the prompt:
1. Run bottom_gate_ramp_and_scan start with V1, stop at V2, with V_steps. Use scan speed 5e-7 m/s, 128 pixels and 40 lines. Use series name "folder\scan"
2. Run analyze_sxm_dynamics on sxm data in "folder". Identify a a um x b um region. Show a grid with n x m points.
3. Set the scan frame to the a um x b um region with the corrected global angle.
4. Run grid_generic_sweeper with all the grid points. Use 10s wait and basename "folder\grid".
5. Run analyze_twist_angle_disorder on grid data in "folder". Average every 2 files for back and forth.
6. Find the best measurement position based on GWC score.
7. Run grid_generic_sweeper with the best point position. Use 4 iterations and basename "folder\bestPoint".

