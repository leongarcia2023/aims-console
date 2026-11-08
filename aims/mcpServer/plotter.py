"""
plotter.py - matplotlib-based refined scientific line plotting.
"""

from __future__ import annotations

from typing import Any, Optional

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from utils import apply_style, DEFAULT_FONT, DEFAULT_FSIZE, _set_global_font

# Default color cycle
_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]

# ---------------------------------------------------------------------------
# 1.  Line / Time-series plot (Refined)
# ---------------------------------------------------------------------------

def line_plot(
    df: pd.DataFrame,
    x_col: str,
    y_cols: list[str],
    line_styles: Optional[list[dict[str, str]]] = None,
    xlim: Optional[tuple[float, float]] = None,
    ylim: Optional[tuple[float, float]] = None,
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
    grid: bool = False,
    legend: bool = True,
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (10, 6),
    linewidth: float = 1.5,
    alpha: float = 0.9,
) -> matplotlib.figure.Figure:
    """
    Plot one or more y-columns vs a single x-column with custom limits and styling.

    Parameters
    ----------
    df          : DataFrame containing the data.
    x_col       : Column name for the x-axis.
    y_cols      : One or more column names for the y-axis.
    line_styles : List of dicts per y column, e.g. [{"color": "red", "linestyle": "--"}, ...]
    xlim        : (min, max) for x-axis.
    ylim        : (min, max) for y-axis.
    grid        : Whether to draw grid lines (default False).
    """
    _set_global_font(font, fontsize)
    fig, ax = plt.subplots(figsize=figsize)

    x = df[x_col].to_numpy(dtype=float)
    
    # Handle dual Y axes if exactly two columns are provided
    use_twin = (len(y_cols) == 2)
    ax2 = None
    if use_twin:
        ax2 = ax.twinx()

    lines = []
    labels = []

    for i, col in enumerate(y_cols):
        y = df[col].to_numpy(dtype=float)
        
        # Determine style for this line
        style = {}
        if line_styles and i < len(line_styles):
            style = line_styles[i]
        
        color = style.get("color", _COLORS[i % len(_COLORS)])
        linestyle = style.get("linestyle", "-")
        
        target_ax = ax2 if (use_twin and i == 1) else ax
        ln = target_ax.plot(x, y, label=col, linewidth=linewidth,
                           color=color, linestyle=linestyle, alpha=alpha)
        lines.append(ln[0])
        labels.append(col)

        if use_twin and i == 1:
            target_ax.set_ylabel(col, color=color)
            target_ax.tick_params(axis='y', labelcolor=color)

    # Set axis limits if provided
    if xlim:
        ax.set_xlim(xlim)
    if ylim:
        ax.set_ylim(ylim)

    apply_style(
        ax,
        title=title or (f"{', '.join(y_cols)} vs {x_col}"),
        xlabel=xlabel or x_col,
        ylabel=ylabel or (y_cols[0] if not use_twin else y_cols[0]),
        grid=grid,
        legend=False,  # Legend handled manually for twinx
        font=font,
        fontsize=fontsize,
    )

    if legend and labels:
        # Combine legends from both axes
        ax.legend(lines, labels, loc='best', prop={'family': font, 'size': fontsize})

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 2.  Measurement Position Plot
# ---------------------------------------------------------------------------

def position_plot(
    x_m: float,
    y_m: float,
    canvas_size_um: float = 30.0,
    xlim_um: Optional[tuple[float, float]] = None,
    ylim_um: Optional[tuple[float, float]] = None,
    title: str = "Measurement Position",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (8, 8),
    point_color: str = "red",
    point_size: float = 100,
) -> matplotlib.figure.Figure:
    """
    Draw a square canvas (e.g. 30x30 um) and mark the (x, y) position.
    
    Parameters
    ----------
    x_m, y_m       : Coordinates in meters.
    canvas_size_um : Side length of the square canvas in micrometers.
    xlim_um        : (min, max) in micrometers for x-axis zoom.
    ylim_um        : (min, max) in micrometers for y-axis zoom.
    """
    _set_global_font(font, fontsize)
    fig, ax = plt.subplots(figsize=figsize)
    
    # Convert meters to micrometers
    x_um = x_m * 1e6
    y_um = y_m * 1e6
    
    # Plot the point
    ax.scatter([x_um], [y_um], color=point_color, s=point_size, zorder=5, label=f"({x_um:.2f}, {y_um:.2f}) \u03BCm")
    
    # Set axis limits (default to 0 to canvas_size_um if zoom not provided)
    ax.set_xlim(xlim_um if xlim_um else (0, canvas_size_um))
    ax.set_ylim(ylim_um if ylim_um else (0, canvas_size_um))
    
    # Ensure square aspect ratio
    ax.set_aspect('equal', adjustable='box')
    
    apply_style(
        ax,
        title=title,
        xlabel="X (\u03BCm)",
        ylabel="Y (\u03BCm)",
        grid=True,  # Helpful for coordinate context
        legend=False,
        font=font,
        fontsize=fontsize,
    )
    
    # Add a thin border/box for the canvas
    rect = matplotlib.patches.Rectangle((0, 0), canvas_size_um, canvas_size_um, 
                                        linewidth=1, edgecolor='black', facecolor='none', alpha=0.3)
    ax.add_patch(rect)
    
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 3.  Combined Position & Measurement Plot
# ---------------------------------------------------------------------------

def combined_plot(
    data_list: list[tuple[float, float, pd.DataFrame, str]],
    x_col: str,
    y_cols: list[str],
    canvas_size_um: float = 30.0,
    xlim_um: Optional[tuple[float, float]] = None,
    ylim_um: Optional[tuple[float, float]] = None,
    xlim_line: Optional[tuple[float, float]] = None,
    ylim_line: Optional[tuple[float, float]] = None,
    title: str = "Combined Position & Measurement",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (16, 7),
    line_styles: Optional[list[dict[str, str]]] = None,
    point_size: float = 120,
) -> matplotlib.figure.Figure:
    """
    1x2 subplots: [Position Map] | [1D Data Plot]
    
    Parameters
    ----------
    data_list : list of (x_m, y_m, df, label) tuples.
    x_col     : X column for the line plot.
    y_cols    : list of Y columns for the line plot (per file).
    """
    _set_global_font(font, fontsize)
    fig, (ax_pos, ax_line) = plt.subplots(1, 2, figsize=figsize)
    
    # Left: Position Map
    for i, (x_m, y_m, _, label) in enumerate(data_list):
        # Resolve color
        style = line_styles[i] if line_styles and i < len(line_styles) else {}
        color = style.get("color", _COLORS[i % len(_COLORS)])
        
        x_um, y_um = x_m * 1e6, y_m * 1e6
        ax_pos.scatter([x_um], [y_um], color=color, s=point_size, zorder=5, label=label)

    # Set axis limits (default to 0 to canvas_size_um if zoom not provided)
    ax_pos.set_xlim(xlim_um if xlim_um else (0, canvas_size_um))
    ax_pos.set_ylim(ylim_um if ylim_um else (0, canvas_size_um))
    ax_pos.set_aspect('equal', adjustable='box')
    
    apply_style(
        ax_pos, title="Measurement Locations", 
        xlabel="X (\u03BCm)", ylabel="Y (\u03BCm)", 
        grid=True, legend=True, font=font, fontsize=fontsize
    )
    
    rect = matplotlib.patches.Rectangle((0, 0), canvas_size_um, canvas_size_um, 
                                        linewidth=1, edgecolor='black', facecolor='none', alpha=0.3)
    ax_pos.add_patch(rect)

    # Right: 1D Data Plot
    # Handle dual Y axes if exactly two columns are provided
    use_twin = (len(y_cols) == 2)
    ax_line2 = None
    if use_twin:
        ax_line2 = ax_line.twinx()

    num_styles = len(line_styles) if line_styles is not None else 0
    for i, (_, _, df, label) in enumerate(data_list):
        style = line_styles[i] if line_styles is not None and i < num_styles else {}
        color = style.get("color", _COLORS[i % len(_COLORS)])
        ls = style.get("linestyle", "-")
        
        x = df[x_col].to_numpy(dtype=float)
        for j, y_c in enumerate(y_cols):
            y = df[y_c].to_numpy(dtype=float)
            # Label includes file label + col name if multiple Y cols
            curve_label = label if len(y_cols) == 1 else f"{label} - {y_c}"
            
            target_ax = ax_line2 if (use_twin and j == 1) else ax_line
            target_ax.plot(x, y, label=curve_label, color=color, linestyle=ls, alpha=0.9, linewidth=1.5)

            if use_twin and j == 1:
                target_ax.set_ylabel(y_c, color=color)
                target_ax.tick_params(axis='y', labelcolor=color)

    if xlim_line: ax_line.set_xlim(xlim_line)
    if ylim_line: ax_line.set_ylim(ylim_line)
    
    apply_style(
        ax_line, title="Measurement Data", 
        xlabel=x_col, ylabel=y_cols[0] if len(y_cols) == 1 or use_twin else "Value",
        grid=False, legend=False, font=font, fontsize=fontsize
    )

    if use_twin:
        # Combine handles for legend
        h1, l1 = ax_line.get_legend_handles_labels()
        h2, l2 = ax_line2.get_legend_handles_labels()
        ax_line.legend(h1+h2, l1+l2, loc='best', prop={'family': font, 'size': fontsize})
    else:
        ax_line.legend(loc='best', prop={'family': font, 'size': fontsize})

    fig.suptitle(title, fontsize=fontsize + 2, fontname=font)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 4.  2D Image Plot (Heatmap)
# ---------------------------------------------------------------------------

def image_plot(
    data: np.ndarray,
    width_um: float,
    height_um: float,
    title: str = "",
    xlabel: str = "X (\u03BCm)",
    ylabel: str = "Y (\u03BCm)",
    cmap: str = "viridis",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (10, 8),
    xlim: Optional[tuple[float, float]] = None,
    ylim: Optional[tuple[float, float]] = None,
    cbar_shrink: float = 1.0,
    cbar_label: str = "",
    show_title: bool = True,
) -> matplotlib.figure.Figure:
    """
    Plot a 2D heatmap of the scan data.

    Parameters
    ----------
    data      : 2D numpy array.
    width_um  : Width in micrometers.
    height_um : Height in micrometers.
    """
    _set_global_font(font, fontsize)
    fig, ax = plt.subplots(figsize=figsize)

    # Use extent to scale the axes to the real-world size in um
    im = ax.imshow(data, extent=[0, width_um, 0, height_um], cmap=cmap, origin='lower')

    # Trim axes if provided
    if xlim: ax.set_xlim(xlim)
    if ylim: ax.set_ylim(ylim)

    # Add colorbar
    cbar = fig.colorbar(im, ax=ax, shrink=cbar_shrink)
    cbar.ax.tick_params(labelsize=fontsize)
    if cbar_label:
        cbar.set_label(cbar_label, fontsize=fontsize, fontname=font)
    for label in cbar.ax.get_yticklabels():
        label.set_fontname(font)

    apply_style(
        ax,
        title=title if show_title else None,
        xlabel=xlabel,
        ylabel=ylabel,
        grid=False,
        legend=False,
        font=font,
        fontsize=fontsize,
    )

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 5.  SXM Combined Plot (SXM Image + Points Overlay + 1D Data)
# ---------------------------------------------------------------------------

def sxm_combined_plot(
    sxm_data: np.ndarray,
    sxm_meta: dict[str, Any],
    data_list: list[tuple[float, float, pd.DataFrame, str]],
    x_col: str,
    y_cols: list[str],
    title: str = "SXM Combined Overlay",
    cmap: str = "bone",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (20, 10),
    line_styles: Optional[list[dict[str, str]]] = None,
    point_size: float = 120,
    x_min: Optional[float] = None,
    x_max: Optional[float] = None,
    y_min: Optional[float] = None,
    y_max: Optional[float] = None,
    show_legend: bool = True,
    color_by_column: bool = False,
    use_subplots: bool = False,
    max_subplot_rows: int = 4,
    angle_offset: float = 0.0,
    normalize: bool = False,
    norm_vmin: float = 0.0,
    norm_vmax: float = 7.0,
    pts_per_row: Optional[int] = None,
) -> matplotlib.figure.Figure:
    """
    1x2 subplots:
    [Left: SXM Image with Points Overlay] | [Right: 1D Data Plot]
    """
    n_pts = len(data_list)
    # Deduce rows/cols if subplots needed
    if pts_per_row is None:
        pts_per_row = n_pts

    total_rows = 1
    if use_subplots or color_by_column:
        if pts_per_row == n_pts:
            n_side = int(np.ceil(np.sqrt(n_pts)))
            pts_per_row = n_side

        total_rows = int(np.ceil(n_pts / pts_per_row)) if pts_per_row > 0 else 1

    if not use_subplots:
        fig, (ax_img, ax_line) = plt.subplots(1, 2, figsize=figsize)
        ax_list = [ax_line]
        row_mapping = [0] * n_pts
        col_mapping = list(range(n_pts))
    else:
        fig = plt.figure(figsize=figsize)
        layout_cols = int(np.ceil(total_rows / max_subplot_rows))
        layout_rows = min(total_rows, max_subplot_rows)
        # Wider ratio for the SXM panel
        gs = fig.add_gridspec(layout_rows, 1 + layout_cols, width_ratios=[2.5] + [1.0]*layout_cols)
        ax_img = fig.add_subplot(gs[:, 0])
        ax_list = []
        for r_idx in range(total_rows):
            gs_row = r_idx % max_subplot_rows
            gs_col = 1 + (r_idx // max_subplot_rows)
            ax_list.append(fig.add_subplot(gs[gs_row, gs_col]))
        row_mapping = [i // pts_per_row for i in range(n_pts)]
        col_mapping = [i % pts_per_row for i in range(n_pts)]

    import scipy.ndimage
    # 1. Coordinate & Resampling Setup
    scan_angle_deg = sxm_meta.get("scan_angle", 0.0)
    total_angle = scan_angle_deg + angle_offset
    theta = np.radians(total_angle)

    rng_x = sxm_meta["scan_range_x"] * 1e6
    rng_y = sxm_meta["scan_range_y"] * 1e6
    off_x = sxm_meta["scan_offset_x"] * 1e6
    off_y = sxm_meta["scan_offset_y"] * 1e6

    pixels_y, pixels_x = sxm_data.shape
    psize_x = rng_x / pixels_x
    psize_y = rng_y / pixels_y

    # Resample to square pixels if aspect is non-trivial
    if not np.isclose(psize_x, psize_y, rtol=1e-3):
        # Target the finer resolution for resampling
        target_psize = min(psize_x, psize_y)
        new_w = int(rng_x / target_psize)
        new_h = int(rng_y / target_psize)
        zoom_x = new_w / pixels_x
        zoom_y = new_h / pixels_y
        resampled_data = scipy.ndimage.zoom(sxm_data, (zoom_y, zoom_x), order=1)
    else:
        resampled_data = sxm_data

    # Rotate the square-pixel data into Laboratory Frame
    rotated_pixels = scipy.ndimage.rotate(resampled_data, total_angle, reshape=True, order=1)

    # Calculate bounding extent of the rotated scan area
    c_x = np.array([-rng_x/2, rng_x/2, rng_x/2, -rng_x/2])
    c_y = np.array([-rng_y/2, -rng_y/2, rng_y/2, rng_y/2])
    rot_c_x = c_x * np.cos(theta) - c_y * np.sin(theta)
    rot_c_y = c_x * np.sin(theta) + c_y * np.cos(theta)

    ext_w = np.max(rot_c_x) - np.min(rot_c_x)
    ext_h = np.max(rot_c_y) - np.min(rot_c_y)
    rotated_extent = [off_x - ext_w/2, off_x + ext_w/2, off_y - ext_h/2, off_y + ext_h/2]

    # Determine color limits before rotation padding zeroes distort them
    valid_vals = resampled_data[np.isfinite(resampled_data)]
    if len(valid_vals) > 0:
        c_vmin, c_vmax = np.nanpercentile(valid_vals, [2, 98])
    else:
        c_vmin, c_vmax = None, None

    im = ax_img.imshow(
        rotated_pixels,
        cmap=cmap,
        extent=rotated_extent,
        origin='lower',
        vmin=c_vmin,
        vmax=c_vmax
    )
    cbar = fig.colorbar(im, ax=ax_img, pad=0.02, shrink=0.8)
    cbar.ax.tick_params(labelsize=fontsize*0.8)

    ax_img.set_xlabel("X (µm)")
    ax_img.set_ylabel("Y (µm)")
    # Overlay & plotting
    from matplotlib import colormaps
    cmap_coolwarm = colormaps["coolwarm"]
    num_styles = len(line_styles) if line_styles is not None else 0

    for i, (x_m, y_m, df, label) in enumerate(data_list):
        xm_um, ym_um = x_m * 1e6, y_m * 1e6

        # Color logic
        col_idx = col_mapping[i]
        c_denom = max(1, pts_per_row - 1)
        color = cmap_coolwarm(col_idx / c_denom) if color_by_column else 'red'

        # Overlay dot in Lab Frame (microns)
        ax_img.scatter(xm_um, ym_um, color=color, s=point_size, edgecolors='white', zorder=10)

        # Plot curve
        target_ax = ax_list[row_mapping[i]]
        ls_dict = line_styles[i % num_styles] if num_styles > 0 else {}
        ax_sub_label = label if not use_subplots else None

        y_vals = df[y_cols[0]].to_numpy()
        x_vals = df[x_col].to_numpy()

        if normalize:
            y0 = np.interp(norm_vmin, x_vals, y_vals)
            y7 = np.interp(norm_vmax, x_vals, y_vals)
            if not np.isclose(y7, y0):
                y_plot = (y_vals - y0) / (y7 - y0)
            else:
                y_plot = y_vals - y0
        else:
            y_plot = y_vals

        target_ax.plot(df[x_col], y_plot, label=ax_sub_label, color=color, **ls_dict)

    # Annotate the first and last row on the spatial overlay
    if len(data_list) > 0:
        # First Row
        x0_um, y0_um = data_list[0][0] * 1e6, data_list[0][1] * 1e6
        ax_img.annotate('Row 1', xy=(x0_um, y0_um), xytext=(-30, 0),
                        textcoords='offset points', color='white', fontsize=max(9, fontsize*0.8),
                        fontweight='bold', ha='right', va='center',
                        arrowprops=dict(arrowstyle='->', color='white'))

        # Last Row
        idx_last_row = (total_rows - 1) * pts_per_row
        if 0 <= idx_last_row < len(data_list):
            xL_um, yL_um = data_list[idx_last_row][0] * 1e6, data_list[idx_last_row][1] * 1e6
            ax_img.annotate(f'Row {total_rows}', xy=(xL_um, yL_um), xytext=(-30, 0),
                            textcoords='offset points', color='white', fontsize=max(9, fontsize*0.8),
                            fontweight='bold', ha='right', va='center',
                            arrowprops=dict(arrowstyle='->', color='white'))


    # Styles
    ax_img.set_aspect('equal')
    apply_style(
        ax_img, title="Spatial Overlay (Lab Frame)",
        xlabel="X (\u03BCm)", ylabel="Y (\u03BCm)",
        grid=False, legend=False, font=font, fontsize=fontsize
    )

    for r_idx, ax in enumerate(ax_list):
        apply_style(
            ax, title=f"Row {r_idx + 1}" if use_subplots else "Line Plot",
            xlabel=x_col if (r_idx == len(ax_list)-1 or not use_subplots) else "",
            ylabel="Signal (V)",
            grid=False, legend=(not use_subplots and show_legend), font=font, fontsize=fontsize
        )
        if x_min is not None or x_max is not None: ax.set_xlim(x_min, x_max)
        if y_min is not None or y_max is not None: ax.set_ylim(y_min, y_max)

    if title:
        fig.suptitle(title)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 6.  2D Colormap (Heatmap from 1D Series)
# ---------------------------------------------------------------------------

def heatmap_2D(
    matrix: np.ndarray,
    x_vec: np.ndarray,
    y_vec: np.ndarray,
    title: str = "",
    xlabel: str = "X",
    ylabel: str = "Y",
    zlabel: str = "Z",
    cmap: str = "viridis",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (10, 8),
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> matplotlib.figure.Figure:
    """
    Plot a 2D heatmap from a matrix with specified X and Y vectors.

    Parameters
    ----------
    matrix : 2D numpy array of shape (len(y_vec), len(x_vec))
    x_vec  : 1D numpy array for the horizontal axis.
    y_vec  : 1D numpy array for the vertical axis.
    """
    _set_global_font(font, fontsize)
    fig, ax = plt.subplots(figsize=figsize)

    x_min, x_max = np.min(x_vec), np.max(x_vec)
    y_min, y_max = np.min(y_vec), np.max(y_vec)

    # extent is [left, right, bottom, top]
    im = ax.imshow(
        matrix,
        extent=[x_min, x_max, y_min, y_max],
        cmap=cmap,
        origin='lower',
        aspect='auto',
        vmin=vmin,
        vmax=vmax
    )

    cbar = fig.colorbar(im, ax=ax)
    cbar.ax.tick_params(labelsize=fontsize)
    cbar.set_label(zlabel, size=fontsize, fontname=font)

    apply_style(
        ax, title=title, xlabel=xlabel, ylabel=ylabel,
        grid=False, legend=False, font=font, fontsize=fontsize
    )

    fig.tight_layout()
    return fig

# ---------------------------------------------------------------------------
# 7.  Similarity Metrics Plot
# ---------------------------------------------------------------------------

def similarity_metrics_plot(
    results_df: pd.DataFrame,
    title: str = "Image Similarity Metrics vs Gate Voltage",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (10, 6),
    save_path: Optional[str] = None,
) -> matplotlib.figure.Figure:
    """
    Plot similarity metrics (LNCC Loss, Pearson Loss, MI, SSIM) vs Gate Voltage.

    Parameters
    ----------
    results_df : DataFrame with columns ['Gate Voltage (V)', 'LNCC Loss', 'Pearson Loss', 'MI', 'SSIM']
    """
    _set_global_font(font, fontsize)
    fig, ax = plt.subplots(figsize=figsize)

    x = results_df['Gate Voltage (V)'].to_numpy(dtype=float)

    metrics = [
        ('LNCC Loss', 'LNCC Loss', '#1f77b4', '-'),
        ('Pearson Loss', 'Pearson Loss', '#ff7f0e', '--'),
        ('MI', 'Mutual Information', '#2ca02c', '-.'),
        ('SSIM', 'SSIM Index', '#d62728', ':')
    ]

    # Check which metrics are present and plot them
    for col_name, label, color, ls in metrics:
        if col_name in results_df.columns:
            y = results_df[col_name].to_numpy(dtype=float)
            ax.plot(x, y, label=label, color=color, linestyle=ls, linewidth=2, marker='o', markersize=4)

    apply_style(
        ax,
        title=title,
        xlabel="Bottom Gate Voltage (V)",
        ylabel="Metric Value / Loss",
        grid=True,
        legend=True,
        font=font,
        fontsize=fontsize
    )

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 8.  Twist Angle Disorder Map
# ---------------------------------------------------------------------------

def twist_angle_map_plot(
    data_list: list[tuple[float, float, pd.DataFrame, str]],
    x_col: str,
    y_col: str,
    pts_per_row: int | None = None,
    # Gate geometry
    has_top_gate: bool = True,
    has_bottom_gate: bool = True,
    top_hbn_thickness_nm: float = 30.0,
    bottom_hbn_thickness_nm: float = 30.0,
    epsilon_hbn: float = 3.5,
    lattice_constant_angstrom: float = 2.46,
    # Dip ranges for filling factors
    nu0_range: tuple[float, float] = (-0.98, 0.20),
    nu1_range: tuple[float, float] | None = None,
    theta_min: float = 2.0,
    theta_max: float = 4.0,
    smooth_window: int = 15,
    nu0_val: float = 0.0,
    nu1_val: float = 1.0,
    input_v0: float | list[float] | None = None,
    cnp_top_n_nu2: int | None = None,
    cnp_nu2_weight: float = 0.5,
    nu2_lo_factor: float = 1.8,
    nu2_hi_factor: float = 2.2,
    nu2_use_shoulder: bool = True,
    hampel_k: float = 3.5,
    hampel_floor_v: float = 0.5,
    # Optional SXM overlay
    sxm_data: np.ndarray | None = None,
    sxm_meta: dict | None = None,
    sxm_cmap: str = "bone",
    angle_offset: float = 0.0,
    # Display
    title: str = "",
    figsize: tuple[float, float] = (12, 5),
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    cmap: str = "plasma",
    show_values: bool = False,
) -> matplotlib.figure.Figure:
    """
    Characterize the twist angle disorder from a 2D grid of gate-sweep measurements.

    For each measurement point, finds the dips at filling factor ν=0 (CNP) and ν=1
    using find_dip_in_range. Converts dip voltages to carrier densities via the
    gate capacitance, derives the moiré unit-cell density n_s = Δn, and inverts
    the moiré lattice formula to obtain the local twist angle θ.

    Physics
    -------
    - Capacitance per area: C/A = ε₀ · ε_hBN / d_hBN
    - Carrier density from gate: n = (C/A) · V / e
    - Total n = n_tg + n_bg (whichever gates are present)
    - Moiré density: n_s = Δn / |ν₁ - ν₀| (where Δn = |n_1 - n_0|)
    - Moiré lattice constant: a_M = sqrt(2 / (sqrt(3) * n_s))  [SI: 1/m²]
    - Twist angle: θ = 2 * arcsin(a₀ / (2 * a_M))
    - If no dip is found: θ = 0 (flagged as invalid)

    Parameters
    ----------
    data_list       : List of (x_m, y_m, df, label) tuples from grid measurements.
    x_col           : Column name for the gate voltage axis.
    y_col           : Column name for the signal (e.g. MIM-Im).
    pts_per_row     : Number of points per row in the measurement grid (auto-detected if None).
    has_top_gate    : Whether a top gate is present.
    has_bottom_gate : Whether a bottom gate is present.
    top_hbn_thickness_nm    : Thickness of top hBN in nm.
    bottom_hbn_thickness_nm : Thickness of bottom hBN in nm.
    epsilon_hbn     : Relative dielectric constant of hBN (default 3.5).
    lattice_constant_angstrom : Lattice constant of twisted material in Å (default graphene 2.46 Å).
    nu0_range       : Voltage range [V_min, V_max] to search for the ν=0 dip.
    nu1_range       : Voltage range [V_min, V_max] to search for the ν=1 dip.
    sxm_data        : Optional 2D array of SXM image pixels.
    sxm_meta        : Optional metadata dict for the SXM file (scan_range, scan_offset, scan_angle).
    sxm_cmap        : Colormap for SXM background image.
    angle_offset    : Additional rotation offset for the SXM image (degrees).
    title           : Figure title.
    figsize         : Figure size in inches.
    font / fontsize : Styling.
    cmap            : Colormap for the twist angle heatmap.
    show_values     : If True, annotate each cell with the twist angle value.
    """
    import scipy.ndimage
    import utils as _tdm

    _set_global_font(font, fontsize)

    # 1. Grid layout
    n_pts = len(data_list)
    if pts_per_row is None:
        pts_per_row = int(round(np.sqrt(n_pts)))
    n_rows = int(np.ceil(n_pts / pts_per_row))

    # 2. Per-point twist angle calculation
    # Detection + anchoring + twist are delegated to the validated method in
    # utils (compute_twist_disorder): prominence-based nu=1 with plateau-step and
    # spatial-consistency overrides, nu=2 + confidence, and an averaged nu=1/nu=2 CNP. The
    # method is fixed here (not switchable); only physics inputs (geometry, search
    # windows, optional input_v0) are forwarded.
    points = []
    for (x_m, y_m, df, label) in data_list:
        if x_col in df.columns and y_col in df.columns:
            points.append((df[x_col].to_numpy(dtype=float), df[y_col].to_numpy(dtype=float)))
        else:
            points.append((np.zeros(1), np.zeros(1)))

    _res = _tdm.compute_twist_disorder(
        points, pts_per_row,
        has_top_gate=has_top_gate, has_bottom_gate=has_bottom_gate,
        top_hbn_thickness_nm=top_hbn_thickness_nm,
        bottom_hbn_thickness_nm=bottom_hbn_thickness_nm,
        epsilon_hbn=epsilon_hbn, lattice_constant_angstrom=lattice_constant_angstrom,
        nu0_range=nu0_range, nu1_range=nu1_range, input_v0=input_v0,
        theta_min=theta_min, theta_max=theta_max, smooth_window=smooth_window,
        cnp_top_n_nu2=cnp_top_n_nu2, cnp_nu2_weight=cnp_nu2_weight,
        nu2_lo_factor=nu2_lo_factor, nu2_hi_factor=nu2_hi_factor,
        nu2_use_shoulder=nu2_use_shoulder,
        hampel_k=hampel_k, hampel_floor_v=hampel_floor_v,
    )
    theta_grid = np.asarray(_res["theta"], dtype=float)
    v0_grid = np.full(n_pts, float(_res["cnp_global"]), dtype=float)

    # 3. Grid mapping
    # Reshape into 2D grid (row-major)
    theta_2d = np.zeros((n_rows, pts_per_row))
    for i in range(n_pts):
        r = i // pts_per_row
        c = i % pts_per_row
        if r < n_rows and c < pts_per_row:
            theta_2d[r, c] = theta_grid[i]

    # Detect X/Y directions from first few points to handle scan orientation
    dx_scan = data_list[1][0] - data_list[0][0] if len(data_list) > 1 else 1.0

    # If X is descending in the scan sequence, flip the grid horizontally
    # so smaller X is on the left.
    if dx_scan < 0:
        theta_2d = np.fliplr(theta_2d)

    all_x_abs = np.array([d[0] * 1e6 for d in data_list])
    all_y_abs = np.array([d[1] * 1e6 for d in data_list])

    # Convert tick labels to physical distance along the scan axes (Scanner frame)
    row0_x, row0_y = all_x_abs[:pts_per_row], all_y_abs[:pts_per_row]
    if dx_scan < 0:
        dist_x = np.sqrt((row0_x[::-1] - row0_x[-1])**2 + (row0_y[::-1] - row0_y[-1])**2)
        col0_x, col0_y = all_x_abs[pts_per_row-1::pts_per_row], all_y_abs[pts_per_row-1::pts_per_row]
    else:
        dist_x = np.sqrt((row0_x - row0_x[0])**2 + (row0_y - row0_y[0])**2)
        col0_x, col0_y = all_x_abs[::pts_per_row], all_y_abs[::pts_per_row]

    dist_y = np.sqrt((col0_x - col0_x[0])**2 + (col0_y - col0_y[0])**2)

    sorted_x_um = dist_x
    sorted_y_um = dist_y

    # Set invalid (0) cells to NaN for display
    theta_display = theta_2d.copy().astype(float)
    theta_display[theta_display == 0] = np.nan

    # 4. Figure layout
    has_sxm = sxm_data is not None and sxm_meta is not None
    if has_sxm:
        fig, (ax_sxm, ax_heat) = plt.subplots(1, 2, figsize=figsize,
                                                gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.12})
    else:
        fig, ax_heat = plt.subplots(1, 1, figsize=figsize)

    # 5. SXM background
    if has_sxm:
        rng_x = sxm_meta["scan_range_x"] * 1e6
        rng_y = sxm_meta["scan_range_y"] * 1e6
        off_x_lab = sxm_meta["scan_offset_x"] * 1e6
        off_y_lab = sxm_meta["scan_offset_y"] * 1e6
        scan_angle_deg = sxm_meta.get("scan_angle", 0.0)
        total_angle = scan_angle_deg + angle_offset

        px_y, px_x = sxm_data.shape
        psize_x = rng_x / px_x
        psize_y = rng_y / px_y
        if not np.isclose(psize_x, psize_y, rtol=1e-3):
            target_psize = min(psize_x, psize_y)
            new_w, new_h = int(rng_x / target_psize), int(rng_y / target_psize)
            sxm_resampled = scipy.ndimage.zoom(sxm_data, (new_h / px_y, new_w / px_x), order=1)
        else:
            sxm_resampled = sxm_data

        sxm_rotated = scipy.ndimage.rotate(sxm_resampled, total_angle, reshape=True, order=1)
        theta_r = np.radians(total_angle)
        c_x = np.array([-rng_x/2, rng_x/2, rng_x/2, -rng_x/2])
        c_y = np.array([-rng_y/2, -rng_y/2, rng_y/2, rng_y/2])
        rot_cx = c_x * np.cos(theta_r) - c_y * np.sin(theta_r)
        rot_cy = c_x * np.sin(theta_r) + c_y * np.cos(theta_r)
        ext_w = np.max(rot_cx) - np.min(rot_cx)
        ext_h = np.max(rot_cy) - np.min(rot_cy)
        extent_sxm = [off_x_lab - ext_w/2, off_x_lab + ext_w/2, off_y_lab - ext_h/2, off_y_lab + ext_h/2]

        # Determine color limits before rotation zeroes distort them
        valid_sxm = sxm_resampled[np.isfinite(sxm_resampled)]
        if len(valid_sxm) > 0:
            s_vmin, s_vmax = np.nanpercentile(valid_sxm, [2, 98])
        else:
            s_vmin, s_vmax = None, None

        ax_sxm.imshow(
            sxm_rotated,
            cmap=sxm_cmap,
            extent=extent_sxm,
            origin='lower',
            vmin=s_vmin,
            vmax=s_vmax
        )

        # Overlay the twist angle map as scatter on SXM
        valid_cmap = plt.get_cmap(cmap)
        theta_finite = theta_grid[theta_grid > 0]
        vmin = np.nanmin(theta_finite) if len(theta_finite) > 0 else 0
        vmax = np.nanmax(theta_finite) if len(theta_finite) > 0 else 1
        for i, (x_m, y_m, _, _label) in enumerate(data_list):
            xm_um, ym_um = x_m * 1e6, y_m * 1e6
            if theta_grid[i] > 0:
                norm_t = (theta_grid[i] - vmin) / (vmax - vmin + 1e-9)
                color = valid_cmap(norm_t)
                ax_sxm.scatter(xm_um, ym_um, color=color, s=80, edgecolors='white', zorder=10)
            else:
                ax_sxm.scatter(xm_um, ym_um, color='gray', s=60, edgecolors='white', zorder=10, marker='x')

        ax_sxm.set_aspect('equal')
        apply_style(ax_sxm, title="SXM + Twist Angle Overlay",
                    xlabel="X (µm)", ylabel="Y (µm)",
                    grid=False, legend=False, font=font, fontsize=fontsize)

    # 6. Twist angle heatmap
    # Use index-based extent to keep cells visually square with aspect='equal'
    extent_cells = [0, pts_per_row, 0, n_rows]

    im = ax_heat.imshow(
        theta_display,
        cmap=cmap,
        origin='lower',
        extent=extent_cells,
        aspect='equal',
        interpolation='nearest'
    )
    cbar = fig.colorbar(im, ax=ax_heat, pad=0.02, shrink=0.9)
    cbar.set_label("Twist Angle (°)", fontsize=fontsize)
    cbar.ax.tick_params(labelsize=fontsize)

    # Optional: annotate cell values (use index coords)
    if show_values:
        for r in range(n_rows):
            for c in range(pts_per_row):
                val = theta_2d[r, c]
                if val > 0:
                    ax_heat.text(c + 0.5, r + 0.5, f"{val:.2f}°", ha='center', va='center',
                                 fontsize=max(7, fontsize * 0.7), color='white',
                                 fontweight='bold')

    # Manually set ticks to um values for physical context
    xticks = np.arange(pts_per_row) + 0.5
    yticks = np.arange(n_rows) + 0.5

    # Use the sorted unique coordinates for labels
    x_labels = [f"{val:.2f}" for val in sorted_x_um]
    y_labels = [f"{val:.2f}" for val in sorted_y_um]

    ax_heat.set_xticks(xticks[::2])
    ax_heat.set_xticklabels(x_labels[::2], rotation=45)
    ax_heat.set_yticks(yticks[::2])
    ax_heat.set_yticklabels(y_labels[::2])

    apply_style(ax_heat, title="",
                xlabel="X (µm)", ylabel="Y (µm)",
                grid=False, legend=False, font=font, fontsize=fontsize)

    # Flip the twist-angle heatmap vertically (first measurement row at the top).
    # invert_yaxis() mirrors the image and its y tick labels together, so every cell
    # keeps the physical distance label that belongs to it.
    ax_heat.invert_yaxis()

    if title:
        fig.suptitle(title, fontsize=fontsize, y=0.98)
    
    # We use subplots_adjust because tight_layout often fails with colorbars and fixed aspect axes
    plt.subplots_adjust(left=0.08, right=0.95, top=0.9, bottom=0.15)
    return fig, theta_grid.tolist(), v0_grid.tolist()


def best_points_map_plot(
    result: dict,
    pts_per_row: int,
    n_best: int = 3,
    title: str = "Best Measurement Points",
    figsize: tuple[float, float] = (16, 5.2),
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    cmap: str = "cividis",
) -> matplotlib.figure.Figure:
    """Three heatmaps -- Mott (nu=1) depth, fractional GWC weight, and the combined
    score -- with the top-``n_best`` measurement points starred. ``result`` is the dict
    returned by ``utils.compute_best_points``.

    A point scores high only when it is strong in BOTH the Mott and the fractional GWC
    (score = geometric mean of the two normalized strengths), so the star sits where the
    Mott and fractional panels are both bright.
    """
    _set_global_font(font, fontsize)
    rows = np.asarray(result["rows"], dtype=int)
    cols = np.asarray(result["cols"], dtype=int)
    n_rows = int(rows.max()) + 1
    n_cols = max(int(cols.max()) + 1, pts_per_row)

    def grid(arr):
        g = np.full((n_rows, n_cols), np.nan)
        a = np.asarray(arr, dtype=float)
        for i in range(len(a)):
            g[rows[i], cols[i]] = a[i]
        return g

    mott = grid(result["mott_depth"])
    frac = grid(result["frac_weight"])
    score = grid(result["score"])
    top = [(b["row"], b["col"], b["rank"]) for b in result["best"][:n_best]]

    fig, axes = plt.subplots(1, 3, figsize=figsize)
    panels = [(mott, "Mott (nu=1) depth (mV)", "magma"),
              (frac, "Fractional GWC weight (mV.fill)", "viridis"),
              (score, "Combined score (Mott x GWC)", cmap)]
    for ax, (gv, t, cm) in zip(axes, panels):
        im = ax.imshow(gv, origin="lower", cmap=cm)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        for (rr, cc, rk) in top:
            ax.scatter([cc], [rr], marker="*", s=460 - (rk - 1) * 90,
                       facecolor="red", edgecolor="k", zorder=6)
            ax.text(cc, rr - 0.62, "#%d" % rk, ha="center", va="top",
                    color="red", fontsize=max(9, fontsize * 0.7), fontweight="bold")
        apply_style(ax, title=t, xlabel="col", ylabel="row",
                    grid=False, legend=False, font=font, fontsize=fontsize)
        ax.set_xticks(range(n_cols))
        ax.set_yticks(range(n_rows))

    if title:
        fig.suptitle(title, fontsize=fontsize)
    plt.subplots_adjust(left=0.05, right=0.97, top=0.9, bottom=0.12, wspace=0.32)
    return fig


def fractional_gwc_score_map_plot(
    result: dict,
    pts_per_row: int,
    n_best: int = 3,
    title: str = "Fractional-GWC Measurement Score",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
    figsize: tuple[float, float] = (15, 6.4),
    cmap: str = "inferno",
) -> matplotlib.figure.Figure:
    """Two heatmaps -- number of fractional GWC dips and the final score -- with the
    top-``n_best`` measurement points marked. ``result`` is the dict returned by
    ``utils.compute_fractional_gwc_score``.
    """
    _set_global_font(font, fontsize)
    rows = np.asarray(result["rows"], dtype=int)
    cols = np.asarray(result["cols"], dtype=int)
    n_rows = int(rows.max()) + 1
    n_cols = max(int(cols.max()) + 1, pts_per_row)

    def grid(arr):
        g = np.full((n_rows, n_cols), np.nan)
        a = np.asarray(arr, dtype=float)
        for i in range(len(a)):
            g[rows[i], cols[i]] = a[i]
        return g

    ndips = np.asarray(result["n_dips"], dtype=float)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=figsize)

    im1 = ax1.imshow(grid(ndips), cmap="viridis", aspect="auto")
    ax1.set_title("N fractional GWC dips (nu=0,1,2 excluded)")
    fig.colorbar(im1, ax=ax1, pad=0.01)
    for i in range(len(rows)):
        ax1.text(cols[i], rows[i], "%d" % int(ndips[i]), ha="center", va="center",
                 color="w", fontsize=max(fontsize - 6, 6))

    im2 = ax2.imshow(grid(result["score"]), cmap=cmap, aspect="auto")
    ax2.set_title("Measurement score" + ("  (N_dips-dominant)" if result.get("ndips_dominant") else ""))
    fig.colorbar(im2, ax=ax2, pad=0.01)
    for b in result["best"][:n_best]:
        ax2.scatter(b["col"], b["row"], s=320, facecolors="none", edgecolors="red", linewidths=2.5)
        ax2.text(b["col"], b["row"] - 0.36, "#%d" % b["rank"], ha="center", va="bottom",
                 color="red", fontsize=max(fontsize - 4, 8), fontweight="bold")

    for ax in (ax1, ax2):
        ax.set_xticks(range(n_cols)); ax.set_xticklabels(range(1, n_cols + 1))
        ax.set_yticks(range(n_rows)); ax.set_yticklabels(["row %d" % (r + 1) for r in range(n_rows)])
    ax2.set_xlabel("grid column")
    if title:
        fig.suptitle(title, fontsize=fontsize)
    plt.subplots_adjust(left=0.06, right=0.98, top=0.9, bottom=0.1, hspace=0.35)
    return fig
