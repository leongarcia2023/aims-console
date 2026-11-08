# Phyiscs-prior: Twisted MoSe2 MIM Analysis

## 1. Sample context

- Material: twisted MoSe2 encapsulated by hBN, MoSe2 lattice constant 0.329 nm.
- Top dielectric: hBN, thickness 6 nm, dielectric constant 3.5.
- Bottom dielectric: hBN, thickness 15 nm.
- Gate: single graphite bottom gate below the bottom hBN.
- Primary probe: microwave impedance microscopy (MIM).
- Nominal optical wavelength in the listed measurements: 750 nm.
- Nominal microwave frequency in the listed measurements: 1 GHz.

The broad scientific goal is to understand correlated insulating and generalized Wigner crystal behavior in this device, including spatial disorder, fractional-filling features, temperature evolution, and possible effective interaction scales.

## 2. Data folders

| Dataset | Folder | Participant-facing description |
|---|---|---|
| Best-position 4.2 K gate sweep | `tMoSe2_BestPosition_GateSweep_Vbg=-1+7V_wl=750nm_1GHz_T=4.2K_B=0T` | Point spectroscopy MIM measurement at the position showing the best correlated feature. All data is taken at the same position and can be averaged. Bottom gate voltage is swept from -1 V to 7 V. Temperature is 4.2 K. Magnetic field is 0 T. For bottom gate voltage greater than 0, the first four correlated dips should correspond to filling factors \(\nu = 1/3, 1/2, 2/3, 1\). The last dip should correspond to filling factor \(\nu = 2\). |
| Correlated-region gate-dependent scans at 4.2 K | `tMoSe2_CorrelatedRegion_scan_Vbg=0.5+4V_wl=750nm_1GHz_T=4.2K_B=0T` | MIM scans across several bottom gate voltages. Bottom gate voltage is swept from 0.5 V to 4 V. Temperature is 4.2 K. Magnetic field is 0 T. The 18th scan has artifacts as the tip retracts further from sample. |
| 8x8 grid spectroscopy at 4.2 K | `tMoSe2_Grid8x8_GateSweep_Vbg=-1+7V_wl=750nm_1GHz_T=4.2K_B=0T` | Point spectroscopy MIM measurement across an 8x8 grid. Measurement is taken at every grid point. Bottom gate voltage is swept from -1 V to 7 V and then swept back to -1 V at each position. Temperature is 4.2 K. Magnetic field is 0 T. Expected twist angle can vary from the best position. Expect charge neutrality matches the best-position at 4.2 K. The \(\nu=1\) feature observed at each grid point can be used to calculate local twist angle. |
| Point temperature-dependent gate sweep | `tMoSe2_Point_GateSweep_Vbg=-1+7V_wl=750nm_1GHz_T=9to20K_B=0T` | Point spectroscopy MIM measurement at a correlated position. Measurement position is not the same as the best position. All data are taken at the same position and can be averaged by temperature. Bottom gate voltage is swept from -1 V to 7 V. Temperature ranges from 9 K to 20 K as labeled in filenames. Magnetic field is 0 T. |
| Temperature-dependent processed scans for ML inversion | `tMoSe2_CorrelatedRegion_scan_Vbg=0.5-2.5V_wl=750nm_1GHz_T=9,11,13,17,20K_B=0T_proceededForML` | Processed MIM scans across several bottom gate voltages. Temperatures are 9 K, 11 K, 13 K, 17 K, and 20 K. Bottom gate voltage is swept from 0.5 V to 2.5 V. Positions of all scans are corrected to show the same region. MIM-Im and MIM-Re channels are separated and saved with `_im` and `_re` suffixes. Magnetic field is 0 T. |

## 3. Literature folder

The literature folder is allowed for both participants. Literature may be used to define formulas, compare energy scales, motivate hypotheses, and interpret mechanisms. It must not replace data analysis. Claims borrowed from literature must be labeled as external context, not as direct evidence from this dataset.

- General MIM review and applications to quantum materials.
- MIM studies of correlated insulator gaps and twist-angle disorder in TMD moire systems.
- MIM, STM, optical, transport, and theory papers relevant to generalized Wigner crystals, melting, and magnetic-field response.
- Papers on MIM signal interpretation, conductivity/compressibility, and neural-network MIM inversion.

## 4. Code and MCP Tool Inventory

The project contains:

- `mcp_server_sse.py`: MCP server with data-analysis tools.
- `utils.py`: dependency/helper functions.
- `plotter.py`: plotting dependency/helper functions.
- `melting_temperature_extraction_tool`: extract the twist angle and melting temperature of generalized Wigner crystal from point spectroscopy data. 
- `meltingAnalysis`: contains essential code for simulating the melting temperature with classical Monte Carlo and quantum Hartree Fock models.

Useful tools listed in the original prompt:

#### 4.1 `average/normalize_measurements_in_folder`

Purpose:

- Average repeated point spectroscopy measurements.
- Normalize point spectroscopy measurements in a folder.

Use for:

- Best-position spectroscopy.
- Temperature-dependent spectroscopy.
- 8x8 grid spectroscopy.

#### 4.2 `analyze correlation/integrated_analyze_grid_correlation`

Purpose:

- Extract positions of correlated features in point spectroscopy by derivative calculation.
- For a folder of point spectroscopy measurements, average and normalize at each position, extract correlated features, and find the position with the best correlated feature.

Use for:

- Feature detection at fractional fillings.
- Selecting robust feature positions.
- Comparing feature amplitude and width.

#### 4.3 `analyze_twist_angle/analyze_twist_angle_disorder`

Purpose:

- Extract twist angle at a single point spectroscopy location.
- Extract twist-angle distribution from grid point spectroscopy.

Use for:

- Best-position twist-angle extraction.
- Temperature-dependent spectroscopy.
- 8x8 twist-angle disorder mapping.

#### 4.4 `plot_sxm_combined`

Purpose:

- Plot SXM scan images together with a grid of point spectroscopy measurements.

Use for:

- Spatial registration between scan maps and grid spectroscopy.

#### 4.5 `analyze_sxm_dynamics/plot_averaged_ssim_map`

Purpose:

- Analyze changes between pairs of SXM/MIM images using difference maps or structural similarity index (SSIM).

Use for:

- Gate-dependent image evolution.
- Temperature-dependent image evolution.
- Magnetic-field-dependent image evolution.

## 5. Experiment capability
- Low temperature MIM measurement from 4K to 20K, cannot go above 20K
- Room temperature atomic force microscopy (AFM) measurement