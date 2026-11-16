"""
utils.py -- shared helpers for the MCP scientific plotting server.

Grouped helpers
---------------
* Data I/O          -- parse and serialize instrument .dat files, load folders,
                       average and linearly normalize curves.
* Figure output     -- render matplotlib figures to base64, raw bytes, or disk.
* Styling           -- global font defaults and uniform axis decoration.
* Signal processing -- Savitzky-Golay smoothing/derivatives, outlier suppression,
                       and derivative-based minima/dip detection.
* SXM images        -- .sxm channel loading, row normalization, stripe removal,
                       scan-artifact detection, and best-window search.
* Similarity        -- LNCC, SSIM, Pearson, and mutual-information image metrics.
* Twist-angle       -- the fixed twist-disorder / GWC method (nu=1/nu=2 anchoring,
                       CNP, twist angle, and best-point ranking).
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from skimage.metrics import structural_similarity as ssim
from skimage.metrics import normalized_mutual_information as nmi
import scipy.ndimage as ndimage

# ---------------------------------------------------------------------------
# Font / style defaults
# ---------------------------------------------------------------------------
DEFAULT_FONT   = "Arial"
DEFAULT_FSIZE  = 15


def _set_global_font(font: str = DEFAULT_FONT, size: int = DEFAULT_FSIZE) -> None:
    """Apply font settings globally to matplotlib."""
    matplotlib.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [font, "DejaVu Sans"],
            "font.size": size,
            "axes.titlesize": size,
            "axes.labelsize": size,
            "xtick.labelsize": size,
            "ytick.labelsize": size,
            "legend.fontsize": size - 1,
        }
    )


# ---------------------------------------------------------------------------
# Data parser  (instrument .dat / .txt format)
# ---------------------------------------------------------------------------

def parse_instrument_data(text: str) -> tuple[dict[str, str], pd.DataFrame]:
    """
    Parse a two-section instrument file.

    Section 1 (before ``[DATA]``) -- key/value metadata pairs separated by
    a tab character. Blank lines and lines with only whitespace are skipped.

    Section 2 (after ``[DATA]``) -- tab-separated numerical data whose first
    row is the column header.

    Returns
    -------
    metadata : dict[str, str]
        Header key -> value pairs (both trimmed of whitespace).
    df : pandas.DataFrame
        Numeric data with column names taken from the header row of the
        [DATA] section.  Values are stored as ``float64`` where possible.
    """
    lines = text.splitlines()

    # Split on the [DATA] marker
    try:
        data_idx = next(
            i for i, line in enumerate(lines) if line.strip().upper() == "[DATA]"
        )
    except StopIteration:
        raise ValueError("Could not find a [DATA] marker in the supplied text.")

    # --- Metadata (header) section ---
    metadata: dict[str, str] = {}
    for line in lines[:data_idx]:
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", maxsplit=1)
        key = parts[0].strip()
        value = parts[1].strip() if len(parts) > 1 else ""
        if key:
            metadata[key] = value

    # --- [DATA] section ---
    data_lines = [l for l in lines[data_idx + 1 :] if l.strip()]
    if not data_lines:
        raise ValueError("No data rows found after [DATA] marker.")

    # First non-empty line after [DATA] is the column header
    # We strip and filter out completely empty strings (trailing tabs)
    header_line = data_lines[0]
    col_names = [h.strip() for h in header_line.split("\t") if h.strip()]
    num_cols = len(col_names)

    # Parse numeric rows
    rows: list[list[float | None]] = []
    for line in data_lines[1:]:
        raw = line.split("\t")
        # Strip each cell and keep only the first 'num_cols' entries
        # This handles trailing tabs or extra columns in the data block
        row_vals = [c.strip() for c in raw]

        parsed_row: list[float | None] = []
        for j in range(num_cols):
            cell = row_vals[j] if j < len(row_vals) else ""
            try:
                parsed_row.append(float(cell))
            except (ValueError, TypeError):
                parsed_row.append(None)
        rows.append(parsed_row)

    df = pd.DataFrame(rows, columns=col_names)

    # Convert all columns to numeric type, converting errors to NaN
    # (Matplotlib handles NaNs gracefully in plots)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return metadata, df


# ---------------------------------------------------------------------------
# Figure encoding / saving
# ---------------------------------------------------------------------------

def encode_figure(
    fig: matplotlib.figure.Figure,
    dpi: int = 300,
    fmt: str = "png",
) -> str:
    """Render *fig* to bytes and return a base64-encoded string."""
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def figure_to_bytes(
    fig: matplotlib.figure.Figure,
    dpi: int = 300,
    fmt: str = "png",
) -> bytes:
    """Render *fig* to bytes and return raw bytes."""
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
    return buf.getvalue()


def save_or_encode(
    fig: matplotlib.figure.Figure,
    save_path: str | None = None,
    dpi: int = 300,
) -> dict[str, Any]:
    """Save *fig* to *save_path* (when given) and return its base64 PNG plus raw bytes.

    Returns a dict with keys ``saved_path``, ``image_base64``, and ``image_bytes``.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    raw_bytes = buf.getvalue()
    b64 = base64.b64encode(raw_bytes).decode("utf-8")

    path_result = None
    if save_path:
        save_path = os.path.abspath(save_path)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        # We can write the bytes directly to avoid re-rendering
        with open(save_path, "wb") as f:
            f.write(raw_bytes)
        path_result = save_path

    plt.close(fig)
    return {"saved_path": path_result, "image_base64": b64, "image_bytes": raw_bytes}


# ---------------------------------------------------------------------------
# Axis decoration helper
# ---------------------------------------------------------------------------

def apply_style(
    ax: matplotlib.axes.Axes,
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
    grid: bool = True,
    legend: bool = True,
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FSIZE,
) -> None:
    """Apply common axis decoration."""
    fp = {"fontname": font, "fontsize": fontsize}
    if title:
        ax.set_title(title, **fp)
    if xlabel:
        ax.set_xlabel(xlabel, **fp)
    if ylabel:
        ax.set_ylabel(ylabel, **fp)
    ax.tick_params(labelsize=fontsize)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontname(font)
    if grid:
        ax.grid(True, linestyle="--", alpha=0.5)
    if legend:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(handles, labels, fontsize=fontsize - 1)


# ---------------------------------------------------------------------------
# Savitzky-Golay Filter Implementation
# ---------------------------------------------------------------------------

def savitzky_golay(y, window_size, order, deriv=0, rate=1):
    r"""Smooth (and optionally differentiate) data with a Savitzky-Golay filter.
    The Savitzky-Golay filter removes high frequency noise from data.
    It has the advantage of preserving the original shape and
    features of the signal better than other types of filtering
    approaches, such as moving averages techniques.
    Parameters
    ----------
    y : array_like, shape (N,)
        the values of the time history of the signal.
    window_size : int
        the length of the window. Must be an odd integer number.
    order : int
        the order of the polynomial used in the filtering.
        Must be less than `window_size` - 1.
    deriv: int
        the order of the derivative to compute
        (default = 0 means only smoothing)
    Returns
    -------
    ys : ndarray, shape (N)
        the smoothed signal (or its n-th derivative).
    """
    from math import factorial

    try:
        window_size = np.abs(int(window_size))
        order = np.abs(int(order))
    except ValueError:
        raise ValueError("window_size and order have to be of type int")
    if window_size % 2 != 1 or window_size < 1:
        raise TypeError("window_size size must be a positive odd number")
    if window_size < order + 2:
        raise TypeError("window_size is too small for the polynomials order")
    order_range = range(order + 1)
    half_window = (window_size - 1) // 2
    # precompute coefficients
    b = np.asmatrix([[k**i for i in order_range] for k in range(-half_window, half_window + 1)])
    m = np.linalg.pinv(b).A[deriv] * rate**deriv * factorial(deriv)
    # pad the signal at the extremes with
    # values taken from the signal itself
    firstvals = y[0] - np.abs(y[1:half_window + 1][::-1] - y[0])
    lastvals = y[-1] + np.abs(y[-half_window - 1:-1][::-1] - y[-1])
    y = np.concatenate((firstvals, y, lastvals))
    return np.convolve(m[::-1], y, mode='valid')


# ---------------------------------------------------------------------------
# SXM Processing (Background Subtraction / Loading)
# ---------------------------------------------------------------------------

def remove_stripes_row_poly(img: np.ndarray, deg: int = 1, mask: np.ndarray | None = None) -> np.ndarray:
    """
    Remove horizontal stripes using row-wise polynomial fitting.
    Parameters:
    - deg: polynomial degree (1 or 2 usually enough)
    - mask: optional boolean mask (True = ignore during fit)
    """
    ny, nx = img.shape
    x = np.arange(nx)
    out = img.copy()

    for i in range(ny):
        val_y = img[i]
        if mask is not None:
            valid = ~mask[i]
            if np.any(valid):
                coeff = np.polyfit(x[valid], val_y[valid], deg)
            else:
                coeff = [0] * (deg + 1)
        else:
            coeff = np.polyfit(x, val_y, deg)

        background = np.polyval(coeff, x)
        out[i] -= background
    return out


def load_sxm_data(path: str, channel: str, direction: str = "forward") -> tuple[np.ndarray, dict]:
    """
    Load a channel from an .sxm file using robust manual extraction, avoiding pySPM bugs
    with specific file sizes. Returns (pixels, metadata).
    """
    import pySPM
    import numpy as np

    sxm = pySPM.SXM(path)
    h = sxm.header

    def parse_header_v(key: str, default_list: list[float]) -> list[float]:
        val = h.get(key)
        if val is None: return default_list
        try:
            if isinstance(val, list):
                target = val[0] if isinstance(val[0], list) else val
                return [float(x) for x in target]
            return [float(val)]
        except (ValueError, TypeError, IndexError):
            return default_list

    sr = parse_header_v('SCAN_RANGE', [0.0, 0.0])
    so = parse_header_v('SCAN_OFFSET', [0.0, 0.0])
    sa_list = parse_header_v('SCAN_ANGLE', [0.0])
    sa = sa_list[0] if sa_list else 0.0

    meta = {
        "width": sxm.size['real']['x'],
        "height": sxm.size['real']['y'],
        "unit": sxm.size['real']['unit'],
        "pixels_x": sxm.size['pixels']['x'],
        "pixels_y": sxm.size['pixels']['y'],
        "channel": channel,
        "direction": direction,
        "scan_range_x": sr[0] if len(sr) > 0 else 0.0,
        "scan_range_y": sr[1] if len(sr) > 1 else 0.0,
        "scan_offset_x": so[0] if len(so) > 0 else 0.0,
        "scan_offset_y": so[1] if len(so) > 1 else 0.0,
        "scan_angle": sa
    }

    # Robust manual extraction
    nx, ny = sxm.size['pixels']['x'], sxm.size['pixels']['y']
    info = h.get('DATA_INFO', [])

    target_idx = -1
    # DATA_INFO usually has a header row, so find where the actual channels start.
    # Row format is typically: ['Channel', 'Name', 'Unit', 'Direction', ...]
    start_row = 1 if len(info) > 0 and info[0][0] == 'Channel' else 0

    frame_offset = 0
    for row in info[start_row:]:
        ch_name = row[1]
        ch_dir = row[3]

        is_match = False
        # Allow flexible matching, e.g. 'MIM-Im AC-X' vs 'MIM-Im_AC-X'
        if channel == ch_name or channel.replace(' ', '_') == ch_name.replace(' ', '_'):
            is_match = True

        if is_match:
            if ch_dir == 'both':
                target_idx = frame_offset if direction == 'forward' else frame_offset + 1
            else:
                target_idx = frame_offset
            break

        if ch_dir == 'both':
            frame_offset += 2
        else:
            frame_offset += 1

    if target_idx == -1:
        # Fallback to pySPM directly if we can't find it manually
        scan = sxm.get_channel(channel, direction=direction)
        return scan.pixels, meta

    # Manual byte extraction
    with open(path, 'rb') as f:
        content = f.read()

    header_end = content.find(b'\x1a\x04') + 2
    raw_data = content[header_end:]

    arrays = np.frombuffer(raw_data, dtype='>f4')
    n_frames = len(raw_data) // (nx * ny * 4)

    if target_idx < n_frames:
        frames = arrays.reshape((n_frames, ny, nx))

        # SXM stores the fast scan axis horizontally; pySPM's get_channel returns
        # an (y, x) image, so the reshaped frame already matches shape (ny, nx).
        img = frames[target_idx]

        # Note: Nanonis may store rows bottom-to-top or top-to-bottom, and pySPM's
        # get_channel flips based on the scan 0-point. We return the bare array.
        return img, meta
    else:
        # Fallback
        scan = sxm.get_channel(channel, direction=direction)
        return scan.pixels, meta


def normalize_image_rows(
    data: np.ndarray,
    ref_col_idx: int = -1
) -> np.ndarray:
    """
    Normalize each row of the given 2D image array to the specified reference column.

    Args:
        data: 2D numpy array containing image pixels
        ref_col_idx: Index of the column to normalize against (default: -1, rightmost)

    Returns:
        A new 2D numpy array where every row is shifted so its value at
        ref_col_idx is 0.
    """
    if len(data.shape) != 2:
        raise ValueError("Data must be a 2D numpy array.")

    # Get the reference column values for all rows
    ref_values = data[:, ref_col_idx]

    # Subtract the reference column from all columns for each row
    # We add a new axis to ref_values to broadcast it correctly across columns
    normalized_data = data - ref_values[:, np.newaxis]

    return normalized_data

def has_horizontal_artifact(pixels: np.ndarray, ratio_threshold: float = 10.0) -> bool:
    """
    Detects if an image array contains horizontal line artifacts.
    This works by checking if the maximum jump between adjacent rows
    is enormously higher than the median row jump, which uniquely
    isolates hard scanning glitches from natural topographic slopes.

    Args:
        pixels: 2D numpy array containing the image data.
        ratio_threshold: The ratio of max row jump to median row jump
                         required to flag an image as anomalous.

    Returns:
        True if an artifact is detected, False otherwise.
    """
    if len(pixels.shape) != 2:
        return False

    # The scan line artifacts are most identifiable after standard row normalization
    n = normalize_image_rows(pixels, ref_col_idx=-1)

    # Calculate absolute difference between adjacent rows
    row_diffs = np.abs(np.diff(n, axis=0))

    # Average jump for each horizontal row transition
    mean_row_diffs = np.mean(row_diffs, axis=1)

    max_diff = np.max(mean_row_diffs)
    med_diff = np.median(mean_row_diffs)

    if med_diff == 0:
        return False

    # Check if the single hardest row transition is a massive localized glitch
    if (max_diff / med_diff) >= ratio_threshold:
        return True

    return False


def find_max_change_window(
    diff_img: np.ndarray,
    um_per_px_y: float,
    um_per_px_x: float,
    win_y_um: float,
    win_x_um: float,
    angle_deg: float = 0.0,
    angle_steps: int = 1
) -> tuple[int, int, float, float]:
    """
    Find the (win_y_um x win_x_um) window in `diff_img` that has the maximum sum.
    If angle_steps > 1, it scans angles from 0 to 180 degrees.
    Uses physical coordinates to ensure the rotating shape preserves its true aspect ratio
    even if the image pixels are not square.

    Returns:
        (center_y_px, center_x_px, max_sum, best_angle)
    """
    import scipy.ndimage
    import numpy as np

    ny, nx = diff_img.shape

    # Candidate angles
    if angle_steps > 1:
        angles = np.linspace(0, 180, angle_steps, endpoint=False)
    else:
        angles = [angle_deg]

    overall_max_sum = -1.0
    best_center = (ny // 2, nx // 2)
    best_angle = angles[0]

    # Suppress scan edges (outer 3%) to avoid flyback/instability artifacts
    margin_y = max(1, int(ny * 0.03))
    margin_x = max(1, int(nx * 0.03))
    clean_img = diff_img.copy()
    clean_img[:margin_y, :] = 0
    clean_img[-margin_y:, :] = 0
    clean_img[:, :margin_x] = 0
    clean_img[:, -margin_x:] = 0

    for angle in angles:
        angle_rad = np.deg2rad(angle)

        # Maximum possible corner distance from center in um (half diagonal)
        diag_um = np.sqrt(win_x_um**2 + win_y_um**2)

        # Max radius in pixels
        diag_px_y = int(np.ceil(diag_um / um_per_px_y))
        diag_px_x = int(np.ceil(diag_um / um_per_px_x))

        # Ensure odd size for clear center
        size_y = diag_px_y * 2 + 1
        size_x = diag_px_x * 2 + 1

        ky, kx = np.indices((size_y, size_x))
        cy, cx = diag_px_y, diag_px_x

        # Physical distances from center
        y_rel = (ky - cy) * um_per_px_y
        x_rel = (kx - cx) * um_per_px_x

        # Rotate back to check if inside unrotated rectangle bounds
        xr = x_rel * np.cos(-angle_rad) - y_rel * np.sin(-angle_rad)
        yr = x_rel * np.sin(-angle_rad) + y_rel * np.cos(-angle_rad)

        mask = (np.abs(xr) < (win_x_um / 2.0)) & (np.abs(yr) < (win_y_um / 2.0))
        kernel = mask.astype(float)

        # Convolve to get sums
        # Using mode='reflect' or 'nearest' might be misleading for sums.
        # 'constant' with cval=0 is safest, but we must normalize by visibility.
        sums = scipy.ndimage.convolve(clean_img, kernel, mode='constant', cval=0.0)

        ones = np.ones_like(diff_img)
        visibility = scipy.ndimage.convolve(ones, kernel, mode='constant', cval=0.0)

        expected_area = kernel.sum()
        # Strictly favor windows that are mostly within the image
        min_visibility = 0.95 * expected_area

        valid_mask = visibility >= min_visibility

        # We use the raw sum without extrapolation to avoid noise boosting at edges
        normalized_sums = np.where(valid_mask, sums, -1.0)

        max_idx = np.unravel_index(np.argmax(normalized_sums), normalized_sums.shape)
        current_max = normalized_sums[max_idx]

        if current_max > overall_max_sum:
            overall_max_sum = current_max
            best_center = (int(max_idx[0]), int(max_idx[1]))
            best_angle = float(angle)

    return best_center[0], best_center[1], float(overall_max_sum), best_angle


# ---------------------------------------------------------------------------
# Dat-file I/O, averaging & normalization
# ---------------------------------------------------------------------------

def read_dat_files_from_folder(folder_path: str) -> list[str]:
    """
    Find all .dat files in the folder and return their raw contents.
    """
    import os
    contents = []
    if not os.path.isdir(folder_path):
        return []

    # Sort files to ensure consistent plotting order (e.g. by name)
    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith('.dat') and os.path.isfile(os.path.join(folder_path, f))])

    for f in files:
        full_path = os.path.join(folder_path, f)
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f_in:
                contents.append(f_in.read())
        except Exception:
            continue
    return contents


def average_dat_contents(content1: str, content2: str, x_col: str = None) -> str:
    """Legacy helper for exactly two files."""
    return average_multiple_dat_contents([content1, content2], x_col=x_col)


def average_multiple_dat_contents(contents: list[str], x_col: str = None) -> str:
    """
    Averages any number of .dat formatted strings.
    The first file defines the master X-grid. All others are interpolated onto it.
    """
    if not contents:
        raise ValueError("No contents provided for averaging.")
    if len(contents) == 1:
        return contents[0]

    parsed_data = [parse_instrument_data(c) for c in contents]
    m0, df0 = parsed_data[0]

    if x_col is None:
        nc = df0.select_dtypes(include=["number"]).columns
        x_col = nc[0] if not nc.empty else None
    if not x_col:
        raise ValueError("No X column found for averaging.")

    # Sort master by X
    df0s = df0.sort_values(by=x_col).reset_index(drop=True)
    x0 = df0s[x_col].values

    # Initialize average dataframe with master X
    avg_df = pd.DataFrame({x_col: x0})

    # Identify numeric columns present in the first file
    numeric_cols = [c for c in df0.columns if c != x_col and pd.api.types.is_numeric_dtype(df0[c])]

    for col in numeric_cols:
        sum_vals = df0s[col].values.copy()
        count = 1
        for i in range(1, len(parsed_data)):
            _, df_i = parsed_data[i]
            if col in df_i.columns and pd.api.types.is_numeric_dtype(df_i[col]):
                df_is = df_i.sort_values(by=x_col)
                xi, zi = df_is[x_col].values, df_is[col].values
                sum_vals += np.interp(x0, xi, zi)
                count += 1
        avg_df[col] = sum_vals / count

    # Average numeric metadata
    avg_meta = m0.copy()
    for k in m0:
        try:
            val_sum = float(m0[k])
            val_count = 1
            for i in range(1, len(parsed_data)):
                mi, _ = parsed_data[i]
                if k in mi:
                    val_sum += float(mi[k])
                    val_count += 1
            avg_meta[k] = f"{val_sum / val_count:.8e}"
        except (ValueError, TypeError):
            # Keep m0 value for non-numeric metadata
            pass

    # Serialize back to .dat format
    res = []
    for k, v in avg_meta.items():
        res.append(f"{k}\t{v}")
    res.append("[DATA]")
    res.append("\t".join(avg_df.columns))

    # Ensure all columns are numeric before serialization
    avg_df = avg_df.apply(pd.to_numeric, errors='coerce')
    data_str = avg_df.to_csv(sep='\t', index=False, header=False, lineterminator='\n', float_format="%.8e")
    res.append(data_str.strip())
    return "\n".join(res)


def normalize_dat_content(
    content: str,
    x_col: str,
    y_col: str,
    target_val1: float = 0.0,
    target_val2: float = 1.0,
    x1_norm: float = None,
    x2_norm: float = None
) -> str:
    """
    Apply a linear transformation Y' = A*Y + B to y_col such that:
    Y(X1) = target_val1
    Y(X2) = target_val2
    If x1_norm/x2_norm are None, use X_min and X_max.
    """
    meta, df = parse_instrument_data(content)

    if x_col not in df.columns or y_col not in df.columns:
        return content

    # Get values at endpoints or specific targets
    if x1_norm is None:
        idx1 = df[x_col].idxmin()
    else:
        idx1 = (df[x_col] - x1_norm).abs().idxmin()

    if x2_norm is None:
        idx2 = df[x_col].idxmax()
    else:
        idx2 = (df[x_col] - x2_norm).abs().idxmin()

    y1_val = df.loc[idx1, y_col]
    y2_val = df.loc[idx2, y_col]

    if pd.isna(y1_val) or pd.isna(y2_val) or y1_val == y2_val:
        return content

    # Solve system:
    # A * y1_val + B = target_val1
    # A * y2_val + B = target_val2

    A = (target_val1 - target_val2) / (y1_val - y2_val)
    B = target_val1 - A * y1_val

    df[y_col] = A * df[y_col] + B

    # Serialize back
    lines = []
    for k, v in meta.items():
        lines.append(f"{k}\t{v}")
    lines.append("[DATA]")
    lines.append("\t".join(df.columns))

    data_str = df.to_csv(sep='\t', index=False, header=False, lineterminator='\n', float_format="%.8e")
    lines.append(data_str.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Signal processing & feature detection
# ---------------------------------------------------------------------------

def analyze_peaks_in_derivative(x: np.ndarray, z: np.ndarray, prominence_factor: float = 0.05) -> dict[str, Any]:
    """
    Calculate derivative and analyze peaks for feature counting and sharpness.

    Returns:
        dict: {num_peaks, total_prominence, max_slope, score}
    """
    from scipy.signal import find_peaks

    if len(x) < 2:
        return {"num_peaks": 0, "total_prominence": 0.0, "max_slope": 0.0, "score": 0.0}

    # 1. Gradient
    # Ensure sorted by x
    sort_idx = np.argsort(x)
    xs, zs = x[sort_idx], z[sort_idx]

    grad = np.gradient(zs, xs)
    abs_grad = np.abs(grad)

    # 2. Peak Detection on Derivative
    # Threshold based on the range of the derivative itself
    g_min, g_max = np.min(abs_grad), np.max(abs_grad)
    g_range = g_max - g_min
    if g_range == 0:
        return {"num_peaks": 0, "total_prominence": 0.0, "max_slope": 0.0, "score": 0.0}

    prom = prominence_factor * g_range
    peaks, properties = find_peaks(abs_grad, prominence=prom)

    num_peaks = len(peaks)
    total_prominence = float(np.sum(properties['prominences'])) if num_peaks > 0 else 0.0
    max_slope = float(g_max)

    # Combined score: emphasize peak count but weight by total prominence (sharpness)
    score = num_peaks * total_prominence

    return {
        "num_peaks": num_peaks,
        "total_prominence": total_prominence,
        "max_slope": max_slope,
        "score": score
    }

def suppress_outliers(z: np.ndarray, factor: float = 5.0) -> np.ndarray:
    """
    Remove single-point spikes using IQR-based detection and linear interpolation.
    """
    if len(z) < 4:
        return z

    import pandas as pd
    s = pd.Series(z)
    q1 = s.quantile(0.25)
    q3 = s.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - factor * iqr
    upper = q3 + factor * iqr

    # Identify outliers
    mask = (s < lower) | (s > upper)
    if not mask.any():
        return z

    s_clean = s.copy()
    s_clean[mask] = np.nan
    # Interpolate and handle edges
    return s_clean.interpolate().bfill().ffill().to_numpy()


def detect_minima_from_derivatives(
    x: np.ndarray,
    z: np.ndarray,
    window_size: int = 21,
    order: int = 3,
    slope_epsilon: float = 0.01,
    slope_threshold: float = 0.0,
    slope_offset: int = 10,
    neighborhood_window: int = 3
) -> dict[str, Any]:
    """
    Detect local minima using 1st and 2nd derivatives.
    Targets points where f'(x) crosses zero with f''(x) > 0.

    Parameters
    ----------
    slope_threshold : float
        Minimum absolute slope (first derivative) required in the
        neighborhood of the zero-crossing to consider it a legitimate feature.
    slope_offset : int
        Number of points to look ahead/behind when checking the slope threshold.
    neighborhood_window : int
        Size of the neighborhood (in points) around a dip to check for
        strict convexity (g2 > 0) and unique zero-crossing of g1.
    """
    # Ensure sorted
    sort_idx = np.argsort(x)
    xs, zs_raw = x[sort_idx], z[sort_idx]

    # 1. Smooth
    zs = savitzky_golay(zs_raw, window_size, order)

    # 2. Derivatives (Direct calculation via Savitzky-Golay for higher precision)
    # Using the sampling rate to get physical units (dZ/dVg)
    dx = xs[1] - xs[0]
    rate = 1.0 / dx
    g1 = savitzky_golay(zs_raw, window_size, order, deriv=1, rate=rate)
    g2 = savitzky_golay(zs_raw, window_size, order, deriv=2, rate=rate)

    # 3. Find zero-crossings of g1 where g2 > 0
    minima_results = []
    for i in range(len(g1) - 1):
        # Precise zero crossing detection via linear interpolation
        if g1[i] <= 0 and g1[i+1] > 0:
            if g2[i] >= 0 or g2[i+1] >= 0:
                # Linear interpolation for zero crossing
                # g1(x) = g1[i] + (x - xs[i]) * (g1[i+1] - g1[i]) / (xs[i+1] - xs[i]) = 0
                denom = g1[i+1] - g1[i]
                if denom != 0:
                    frac = -g1[i] / denom
                    x_exact = xs[i] + frac * (xs[i+1] - xs[i])
                    y_exact = zs[i] + frac * (zs[i+1] - zs[i])
                    g2_exact = g2[i] + frac * (g2[i+1] - g2[i])

                    minima_results.append({
                    "idx": i,
                    "x": x_exact,
                    "y": y_exact,
                    "g2": g2_exact
                })

    # 4. Filter by Slope Threshold and Neighborhood Convexity
    final_features = []
    for feature in minima_results:
        idx = feature["idx"]
        # A. Check neighborhood for significant slope change
        if slope_threshold > 0:
            start_slope = max(0, idx - slope_offset)
            end_slope = min(len(g1), idx + slope_offset + 1)
            neighborhood_max_slope = np.max(np.abs(g1[start_slope:end_slope]))
            if neighborhood_max_slope < slope_threshold:
                continue

        # B. Neighborhood Consistency Check (Stricter Dip Detection)
        if neighborhood_window > 0:
            start_n = max(0, idx - neighborhood_window)
            end_n = min(len(g1), idx + neighborhood_window + 1)

            g1_neighbor = g1[start_n:end_n]
            g2_neighbor = g2[start_n:end_n]

            # 1. Ensure f''(x) > 0 for all points in neighborhood
            if not np.all(g2_neighbor > 0):
                continue

            # 2. Ensure f'(x) crosses zero exactly once in neighborhood
            # (i.e., g1 is strictly increasing)
            if not np.all(np.diff(g1_neighbor) > 0):
                continue

        final_features.append(feature)

    # Prepare outputs
    minima_indices = [f["idx"] for f in final_features]
    x_minima = np.array([f["x"] for f in final_features])
    y_minima = np.array([f["y"] for f in final_features])
    g2_minima = np.array([f["g2"] for f in final_features])

    # Calculate Sharpness (Sum of second derivative at precise minima)
    sharpness = float(np.sum(g2_minima)) if final_features else 0.0
    num_features = len(final_features)

    return {
        "indices": minima_indices,
        "num_features": num_features,
        "x_minima": x_minima,
        "y_minima": y_minima,
        "grad2_minima": g2_minima,
        "sharpness": sharpness,
        "score": (num_features * 1000.0) + sharpness,
        "x_smooth": xs,
        "y_smooth": zs,
        "grad1": g1,
        "grad2": g2
    }

# ---------------------------------------------------------------------------
# Image Similarity Metrics
# ---------------------------------------------------------------------------

def calculate_lncc(img1: np.ndarray, img2: np.ndarray, window_size: int = 7) -> float:
    """
    Calculate the average Local Normalized Cross Correlation between two images.
    """
    # Ensure images are float32
    img1 = img1.astype(np.float32)
    img2 = img2.astype(np.float32)

    # Local means
    kernel = np.ones((window_size, window_size)) / (window_size**2)
    mu1 = ndimage.convolve(img1, kernel, mode='reflect')
    mu2 = ndimage.convolve(img2, kernel, mode='reflect')

    # Local variances and covariance
    mu11 = ndimage.convolve(img1*img1, kernel, mode='reflect')
    mu22 = ndimage.convolve(img2*img2, kernel, mode='reflect')
    mu12 = ndimage.convolve(img1*img2, kernel, mode='reflect')

    sigma1_sq = mu11 - mu1*mu1
    sigma2_sq = mu22 - mu2*mu2
    sigma12 = mu12 - mu1*mu2

    # Add epsilon to avoid division by zero
    eps = 1e-10
    lncc = sigma12 / (np.sqrt(np.maximum(sigma1_sq * sigma2_sq, eps)) + eps)

    return float(np.mean(lncc))


def calculate_lncc_map(img1: np.ndarray, img2: np.ndarray, window_size: int = 7) -> np.ndarray:
    """
    Calculate the Local Normalized Cross Correlation map between two images.
    Returns a 2D numpy array of the same shape as the input images.
    """
    # Ensure images are float32
    img1 = img1.astype(np.float32)
    img2 = img2.astype(np.float32)

    # Local means
    kernel = np.ones((window_size, window_size)) / (window_size**2)
    mu1 = ndimage.convolve(img1, kernel, mode='reflect')
    mu2 = ndimage.convolve(img2, kernel, mode='reflect')

    # Local variances and covariance
    mu11 = ndimage.convolve(img1*img1, kernel, mode='reflect')
    mu22 = ndimage.convolve(img2*img2, kernel, mode='reflect')
    mu12 = ndimage.convolve(img1*img2, kernel, mode='reflect')

    sigma1_sq = mu11 - mu1*mu1
    sigma2_sq = mu22 - mu2*mu2
    sigma12 = mu12 - mu1*mu2

    # Add epsilon to avoid division by zero
    eps = 1e-10
    lncc = sigma12 / (np.sqrt(np.maximum(sigma1_sq * sigma2_sq, eps)) + eps)

    return lncc


def pearson_loss(img1: np.ndarray, img2: np.ndarray) -> float:
    """Calculate 1 - Pearson correlation coefficient."""
    # Flatten images
    v1 = img1.ravel()
    v2 = img2.ravel()

    # pearsonr returns (correlation, p-value)
    corr, _ = pearsonr(v1, v2)
    return 1.0 - float(corr)


def mutual_information(img1: np.ndarray, img2: np.ndarray) -> float:
    """Calculate Normalized Mutual Information."""
    return float(nmi(img1, img2))


def ssim_index(img1: np.ndarray, img2: np.ndarray) -> float:
    """Calculate Structural Similarity Index (SSIM)."""
    # Data range should be handled. Usually SXM data is small (e.g. Volts).
    # We can use the actual range of the images.
    data_range = max(img1.max() - img1.min(), img2.max() - img2.min(), 1e-10)
    return float(ssim(img1, img2, data_range=data_range))


def calculate_ssim_map(img1: np.ndarray, img2: np.ndarray) -> np.ndarray:
    """
    Calculate the Structural Similarity Index (SSIM) map between two images.
    Returns a 2D numpy array the same shape as the input images.
    """
    data_range = max(img1.max() - img1.min(), img2.max() - img2.min(), 1e-10)
    _, ssim_map = ssim(img1, img2, data_range=data_range, full=True)
    return ssim_map


def get_sxm_gate_voltage(path: str, target_name: str = "BottomGate") -> float | None:
    """
    Extract gate voltage from SXM header.
    Looks for the value in 'Outputs' where the name matches 'target_name'.
    """
    import pySPM
    try:
        sxm = pySPM.SXM(path)
        h = sxm.header

        # Look in 'Outputs'
        outputs = h.get('Outputs', {})
        # PySPM's SXM header parsing might structure 'Outputs' differently.
        # Based on inspection, it's often a dict of 'Output N Name' and 'Output N Value'.

        # Let's try to find it by name
        for k, v in h.items():
            if k.startswith('Outputs>Output ') and k.endswith(' Name'):
                # v is usually a list of lists: [['BottomGate', '(V)']]
                name = v[0][0] if isinstance(v, list) and len(v) > 0 and isinstance(v[0], list) else str(v)
                if target_name.lower() in name.lower():
                    # Corresponding value key
                    val_key = k.replace(' Name', ' Value')
                    if val_key in h:
                        val_v = h[val_key]
                        # val_v is usually [['500E-3']]
                        raw_val = val_v[0][0] if isinstance(val_v, list) and len(val_v) > 0 and isinstance(val_v[0], list) else str(val_v)
                        return float(raw_val)

    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Dip Finding for Twist Angle Analysis
# ---------------------------------------------------------------------------

def find_dip_in_range(
    x: np.ndarray,
    y: np.ndarray,
    x_min: float,
    x_max: float,
    smooth_window: int = 5,
) -> tuple[float | None, float | None]:
    """
    Find the most prominent local minimum (dip) within the voltage range [x_min, x_max].

    Strategy:
    1. Restrict x, y to [x_min, x_max].
    2. Smooth with a running average to reduce noise.
    3. Use scipy.signal.find_peaks on (-y) to identify local minima.
    4. Pick the local minimum with the highest prominence.
    5. Fallback: If no local minima are found, return the global minimum in the range.

    Returns
    -------
    (x_dip, y_dip) of the best dip, or (None, None) if no data.
    """
    import scipy.signal

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    # Restrict to range
    mask = (x >= x_min) & (x <= x_max)
    if np.sum(mask) < max(5, smooth_window + 2):
        return None, None

    xw = x[mask]
    yw = y[mask]

    # Smooth the data
    if smooth_window > 1 and len(yw) > smooth_window:
        half = smooth_window // 2
        yw_smooth = np.convolve(yw, np.ones(smooth_window) / smooth_window, mode='same')
        # We work on the inner part to avoid edge effects from convolution
        xw_inner = xw[half:-half]
        yw_inner = yw_smooth[half:-half]
        yw_bare = yw[half:-half]
    else:
        half = 0
        xw_inner = xw
        yw_inner = yw.copy()
        yw_bare = yw

    if len(yw_inner) < 1:
        return None, None

    # Find peaks on inverted signal (peaks of -y are minima of y)
    y_inv = -yw_inner
    peaks, props = scipy.signal.find_peaks(y_inv, prominence=0)

    if len(peaks) > 0:
        # Pick the most prominent peak
        best_peak_idx = peaks[np.argmax(props["prominences"])]
        return float(xw_inner[best_peak_idx]), float(yw_bare[best_peak_idx])

    # Fallback to global minimum in the interior region
    idx_min = int(np.argmin(yw_inner))
    return float(xw_inner[idx_min]), float(yw_bare[idx_min])


def find_dip_via_derivatives(
    x: np.ndarray,
    y: np.ndarray,
    x_min: float,
    x_max: float,
    window_size: int = 11,
    slope_threshold: float = 0.0,
    slope_offset: int = 10,
    neighborhood_window: int = 3
) -> tuple[float | None, float | None]:
    """
    Find a specific dip using the strict rigorous derivative algorithm.
    It identifies ALL derivative zero-crossings natively, then isolates the one
    bounded by [x_min, x_max].
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    # 1. Run the strict derivative engine over the full localized curve
    stats = detect_minima_from_derivatives(
        x, y,
        window_size=window_size,
        slope_threshold=slope_threshold,
        slope_offset=slope_offset,
        neighborhood_window=neighborhood_window
    )

    if len(stats['x_minima']) == 0:
        return None, None

    x_m = stats['x_minima']
    y_m = stats['y_minima']

    # 2. Filter bounds
    mask = (x_m >= x_min) & (x_m <= x_max)
    x_valid = x_m[mask]
    y_valid = y_m[mask]

    if len(x_valid) == 0:
        return None, None

    # 3. Choose the deepest local minimum inside the boundary
    idx_deepest = np.argmin(y_valid)
    return float(x_valid[idx_deepest]), float(y_valid[idx_deepest])


# ---------------------------------------------------------------------------
# Twist-angle disorder method + best measurement points
# ---------------------------------------------------------------------------
# Self-contained (numpy/scipy only): dip detection, nu=1 anchoring with the
# plateau-step and spatial-consistency overrides, nu=2 + confidence,
# nu=1/nu=2 -> averaged CNP -> twist angle, GWC/Mott strength, and best-point
# ranking. The method is fixed; only search windows and gate geometry are inputs.
from dataclasses import dataclass
import scipy.ndimage as ndi
from scipy.signal import find_peaks, savgol_filter

# ---- method internals (fixed; these define the method, not user knobs) ----
SMOOTH = 9                       # light Savitzky-Golay window
POLYORDER = 3
CURV_SMOOTH = 15
PROMINENCE_NOISE = 2.0           # find_peaks prominence floor (noise sigma)
SIGNIFICANCE_NOISE = 2.0         # a dip is real if shoulder depth > this * noise
ABS_DEPTH_FLOOR_V = 2.0e-4       # and also exceeds 0.2 mV
DIST_PTS = 3
SHOULDER_BASELINE_FRACTION = 0.30
DEPTH_HALF_WIDTH_FILL = 0.11     # shoulder window half-width (in nu ~ V via *3.3)
MOTT_STEP_SLOPE_WIN = 21         # slope (derivative) smoothing window
MOTT_STEP_HALF_WIDTH_V = 0.45    # wide shoulder window: spans a plateau
MOTT_STEP_MIN_DROP_V = 0.30      # step must sit this far below the dip pick
V1_DOMAIN_SPLIT = 2.25           # low- vs high-twist Mott split
SPATIAL_NEIGHBOR_MAJORITY = 5    # of 8 neighbours in the other domain to re-assign
SPATIAL_LOW_RANGE = (1.20, 2.10)
SPATIAL_HIGH_RANGE = (2.60, 3.60)
SPATIAL_HAMPEL_K = 3.5           # robust outlier: |V1-neighbour_median| > K*MAD ...
SPATIAL_HAMPEL_FLOOR_V = 0.5     # ... or > this absolute floor (V) -> re-pick V1
NU2_GAP = 0.5                    # nu=2 must lie at least this far above nu=1
NU2_MAX_V = 7.0
SHOULDER_FLAT_FRAC = 0.5         # a nu=2 shoulder's slope must be < this * steepest rise in window
CNP_CONF_THRESH = 10.0           # only well-resolved nu=2 set the CNP
E = 1.602176634e-19
EPS0 = 8.854187817e-12


@dataclass
class Dip:
    """A detected dip in a gate sweep.

    Fields: center voltage ``v``, sample ``index``, shoulder-baseline ``depth`` (V),
    ``curvature`` (second-derivative term), and ``significance`` (depth / noise).
    """
    v: float
    index: int
    depth: float
    curvature: float
    significance: float


def _odd_window(n: int, requested: int) -> int:
    """Return the largest odd window <= ``requested`` that fits a length-``n`` array (>= 5)."""
    win = min(requested, n - (1 - n % 2))
    if win % 2 == 0:
        win -= 1
    return max(win, 5)


def _smooth_and_noise(y, smooth=None):
    """Savitzky-Golay smooth ``y`` and estimate its noise as the MAD of the residual.

    Returns (smoothed, noise_sigma).
    """
    win = _odd_window(len(y), int(smooth) if smooth else SMOOTH)
    ys = savgol_filter(y, win, POLYORDER)
    res = y - ys
    return ys, float(max(1.4826 * np.median(np.abs(res - np.median(res))), 1e-9))


def _curvature(y, dx):
    """Second derivative of ``y`` via a Savitzky-Golay filter (sample spacing ``dx``)."""
    return savgol_filter(y, _odd_window(len(y), CURV_SMOOTH), POLYORDER, deriv=2, delta=dx)


def _shoulder_depth(x, ys, vc, hw, shoulder=SHOULDER_BASELINE_FRACTION):
    """Depth of a dip at ``vc`` relative to a linear shoulder baseline.

    Fits a line to the flanking ``shoulder`` fraction of the window [vc-hw, vc+hw]
    and returns baseline(vc) - signal(vc), clamped at 0.
    """
    if not np.isfinite(vc) or hw <= 0:
        return 0.0
    lo, hi = vc - hw, vc + hw
    m = (x >= lo) & (x <= hi)
    xi, zi = x[m], ys[m]
    if xi.size < 7:
        return 0.0
    w = hi - lo
    left = xi <= lo + shoulder * w
    right = xi >= hi - shoulder * w
    sh = left | right
    if not np.any(left) or not np.any(right) or sh.sum() < 2:
        return 0.0
    coef = np.polyfit(xi[sh], zi[sh], 1)
    return float(max(np.polyval(coef, vc) - np.interp(vc, xi, zi), 0.0))


def _parabolic_center(x, ys, i):
    """Sub-sample dip center from a parabola fit to the points around index ``i``.

    Falls back to ``x[i]`` if the fit is non-convex or the vertex leaves the window.
    """
    lo, hi = max(i - 2, 0), min(i + 3, len(x))
    if hi - lo < 3:
        return float(x[i])
    t = x[lo:hi] - x[i]
    coef = np.polyfit(t, ys[lo:hi], 2)
    if coef[0] <= 0:
        return float(x[i])
    center = float(x[i] - coef[1] / (2.0 * coef[0]))
    return center if (x[lo] <= center <= x[hi - 1]) else float(x[i])


def _detect_dips(x, y, smooth=None):
    """Detect significance-gated dips in a gate sweep.

    Smooths ``y``, finds prominence-gated local minima, refines each center
    parabolically, and keeps those whose shoulder-baseline depth clears both the
    noise-significance and absolute-depth floors. Returns (dips, smoothed, noise).
    """
    dx = float(np.median(np.diff(x)))
    ys, noise = _smooth_and_noise(y, smooth)
    curv = _curvature(y, dx)
    hw = DEPTH_HALF_WIDTH_FILL * 3.3
    peaks, _ = find_peaks(-ys, prominence=PROMINENCE_NOISE * noise, distance=DIST_PTS)
    dips = []
    for p in peaks:
        center = _parabolic_center(x, ys, int(p))
        depth = _shoulder_depth(x, ys, center, hw)
        if depth < SIGNIFICANCE_NOISE * noise or depth < ABS_DEPTH_FLOOR_V:
            continue
        dips.append(Dip(float(center), int(p), float(depth),
                        float(max(curv[int(p)], 0.0)), float(depth / noise)))
    return dips, ys, noise


def _stronger_low_step(x, y, ys, dips, v1_dip, search_range):
    """Plateau-step override for the nu=1 pick.

    Looks for a strong downward slope-step (a Mott plateau) below the current nu=1
    dip within ``search_range``. If its shoulder depth beats the dip's, return the
    step voltage (snapped to a nearby detected dip when available), else None.
    """
    lo, hi = search_range
    dx = float(np.median(np.diff(x)))
    win = _odd_window(len(y), MOTT_STEP_SLOPE_WIN)
    g1 = savgol_filter(y, win, POLYORDER, deriv=1, delta=dx)
    res = y - ys
    noise = 1.4826 * float(np.median(np.abs(res - np.median(res))))
    step_idx, _ = find_peaks(-g1, prominence=1e-9, distance=DIST_PTS)
    steps = [float(x[p]) for p in step_idx
             if lo <= x[p] <= hi and x[p] < v1_dip - MOTT_STEP_MIN_DROP_V]
    if not steps:
        return None
    hwd = MOTT_STEP_HALF_WIDTH_V
    d_dip = _shoulder_depth(x, ys, v1_dip, hwd)
    v_best, d_best = max(((v, _shoulder_depth(x, ys, v, hwd)) for v in steps), key=lambda t: t[1])
    if d_best <= d_dip or d_best < SIGNIFICANCE_NOISE * noise:
        return None
    near = [d.v for d in dips if lo <= d.v <= hi and abs(d.v - v_best) <= MOTT_STEP_MIN_DROP_V]
    return float(min(near, key=lambda dv: abs(dv - v_best)) if near else v_best)


def _pick_mott(x, y, dips, ys, search_range):
    """Pick the nu=1 Mott voltage inside ``search_range``.

    Prefers the deepest, sharpest detected dip; falls back to the window minimum,
    then applies the plateau-step override. Returns (voltage, source_tag).
    """
    lo, hi = search_range
    local = [d for d in dips if lo <= d.v <= hi]
    if local:
        cs = np.median([d.curvature for d in local if d.curvature > 0] or [1.0])
        best = max(local, key=lambda d: d.depth * (1.0 + d.curvature / (cs + 1e-12)))
        v1_dip, src = float(best.v), "mott_local_min"
    else:
        m = (x >= lo) & (x <= hi)
        if np.any(m):
            v1_dip, src = float(x[m][int(np.argmin(ys[m]))]), "mott_window_min"
        else:
            return float(0.5 * (lo + hi)), "mott_range_center"
    v_step = _stronger_low_step(x, y, ys, dips, v1_dip, search_range)
    return (v_step, "mott_step_override") if v_step is not None else (v1_dip, src)


def _pick_cnp(x, ys, dips, v0_range):
    """Pick the charge-neutrality-point voltage inside ``v0_range``.

    Uses the deepest detected dip, or the window minimum if none is found.
    """
    lo, hi = v0_range
    local = [d for d in dips if lo <= d.v <= hi]
    if local:
        return float(max(local, key=lambda d: d.depth).v)
    m = (x >= lo) & (x <= hi)
    return float(x[m][int(np.argmin(ys[m]))]) if np.any(m) else float(0.5 * (lo + hi))


def _spatial_consistency(rows, cols, v1, v1_source, all_dips,
                         hampel_k=SPATIAL_HAMPEL_K, hampel_floor_v=SPATIAL_HAMPEL_FLOOR_V):
    """Enforce spatial consistency of the nu=1 map across the grid.

    Two passes: (1) a low/high-twist domain vote that re-picks a cell's V1 when a
    majority of its neighbours sit in the other domain, and (2) a global Hampel
    outlier pass that re-picks any cell deviating from its neighbour median by more
    than max(k*MAD, floor). Returns (v1, v1_source).
    """
    n = len(v1)
    R, C = int(max(rows)) + 1, int(max(cols)) + 1
    grid = np.full((R, C), np.nan)
    for i in range(n):
        grid[rows[i], cols[i]] = v1[i]
    v1 = v1.copy()
    v1_source = list(v1_source)
    for i in range(n):
        r, c = rows[i], cols[i]
        nb = [grid[r + dr, c + dc]
              for dr in (-1, 0, 1) for dc in (-1, 0, 1)
              if (dr or dc) and 0 <= r + dr < R and 0 <= c + dc < C
              and np.isfinite(grid[r + dr, c + dc])]
        nb = np.array(nb)
        if nb.size < 4:
            continue
        is_high = v1[i] >= V1_DOMAIN_SPLIT
        # majority = a fraction of AVAILABLE neighbours (not a fixed count), so edge/corner
        # cells are corrected too. For 8 interior neighbours this is 5 (unchanged).
        mth = max(3, int(np.ceil(0.6 * nb.size)))
        if is_high and np.sum(nb < V1_DOMAIN_SPLIT) >= mth:
            lo, hi = SPATIAL_LOW_RANGE
        elif (not is_high) and np.sum(nb >= V1_DOMAIN_SPLIT) >= mth:
            lo, hi = SPATIAL_HIGH_RANGE
        else:
            continue
        cand = [d for d in all_dips[i] if lo <= d.v <= hi]
        if cand:
            v1[i] = float(max(cand, key=lambda d: d.depth).v)
            v1_source[i] = "mott_spatial_override"

    # Global robust outlier pass (Hampel): re-pick V1 for ANY cell whose value deviates
    # from its neighbour median by more than max(K*MAD, floor) -- regardless of domain --
    # snapping to the detected dip nearest that median. Catches within-domain outliers that
    # the two-domain rule above cannot (e.g. a lone cell much higher than same-domain nbrs).
    grid2 = np.full((R, C), np.nan)
    for i in range(n):
        grid2[rows[i], cols[i]] = v1[i]
    for i in range(n):
        if not np.isfinite(v1[i]):
            continue
        r, c = rows[i], cols[i]
        nb = np.array([grid2[r + dr, c + dc]
                       for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                       if (dr or dc) and 0 <= r + dr < R and 0 <= c + dc < C
                       and np.isfinite(grid2[r + dr, c + dc])])
        if nb.size < 4:
            continue
        med = float(np.median(nb))
        mad = 1.4826 * float(np.median(np.abs(nb - med)))
        if abs(v1[i] - med) <= max(hampel_k * mad, hampel_floor_v):
            continue
        # Prefer a detected dip near the consensus median; if the curve has none there
        # (the Mott went undetected), fall back to the neighbour median itself.
        snap = max(2.0 * mad, 0.4)
        near = [d for d in all_dips[i] if abs(d.v - med) <= snap]
        v1[i] = float(min(near, key=lambda d: abs(d.v - med)).v) if near else med
        v1_source[i] = "mott_hampel_override"
    return v1, v1_source


def _find_shoulder_nu2(x, y, ys, noise, lo, hi):
    """Detect a nu=2 SHOULDER (a slope-kink on a rising edge, not a true local minimum)
    inside [lo, hi]. A shoulder is a local minimum of the first derivative (the rise
    flattens then steepens again). Its 'depth' is the shoulder-baseline deficit, gated by
    the same significance/floor as dips. Returns (v, depth, significance) or None."""
    if hi <= lo or x.size < 7:
        return None
    dx = float(np.median(np.diff(x)))
    win = _odd_window(len(y), MOTT_STEP_SLOPE_WIN)
    g1 = savgol_filter(y, win, POLYORDER, deriv=1, delta=dx)
    hw = DEPTH_HALF_WIDTH_FILL * 3.3
    inwin = (x >= lo) & (x <= hi)
    gmax = float(np.max(g1[inwin])) if inwin.any() else 0.0   # steepest rise in the window
    kinks, _ = find_peaks(-g1, distance=DIST_PTS)   # slope minima = shoulders
    best = None
    for p in kinks:
        if not (lo <= x[p] <= hi):
            continue
        # Flatness gate: a real nu=2 shoulder is where the rise has genuinely slowed
        # (slope well below the steepest rise in the window). This rejects points that
        # sit on the steep rising edge and only look deep due to curvature.
        if gmax > 0 and g1[p] > SHOULDER_FLAT_FRAC * gmax:
            continue
        vc = _parabolic_center(x, ys, int(p))
        d = _shoulder_depth(x, ys, vc, hw)
        if d < SIGNIFICANCE_NOISE * noise or d < ABS_DEPTH_FLOOR_V:
            continue
        if best is None or d > best[1]:
            best = (float(vc), float(d), float(d / noise))
    return best


def _v_nu1_for_theta(theta_deg, C_total, a0_m):
    """Bottom-gate voltage (measured from the CNP) of the nu=1 Mott at twist angle
    theta_deg, for total gate capacitance C_total. V = n_s*e/C, with the moire density
    n_s = 2/(sqrt3 * a_M^2) and a_M = a0/(2 sin(theta/2))."""
    if C_total <= 0:
        return float("nan")
    a_M = a0_m / (2.0 * np.sin(np.radians(theta_deg) / 2.0))
    n_s = 2.0 / (np.sqrt(3.0) * a_M ** 2)
    return float(n_s * E / C_total)


def compute_twist_disorder(
    points,
    pts_per_row,
    *,
    has_top_gate=False,
    has_bottom_gate=True,
    top_hbn_thickness_nm=15.0,
    bottom_hbn_thickness_nm=15.0,
    epsilon_hbn=3.5,
    lattice_constant_angstrom=3.29,
    nu0_range=(-0.98, 0.20),
    nu1_range=None,
    theta_min=2.0,
    theta_max=4.0,
    input_v0=None,
    cnp_top_n_nu2=None,
    cnp_nu2_weight=0.5,
    nu2_lo_factor=1.8,
    nu2_hi_factor=2.2,
    nu2_use_shoulder=True,
    smooth_window=15,
    hampel_k=SPATIAL_HAMPEL_K,
    hampel_floor_v=SPATIAL_HAMPEL_FLOOR_V,
):
    """Run the fixed twist-disorder method (systematic pipeline).

    nu=1 SEARCH WINDOW is derived from a TWIST-ANGLE range, not raw voltages: with the
    CNP assumed at 0 V, V(nu=1) = n_s(theta)*e/C, so the window is
    [V(nu=1, theta_min), V(nu=1, theta_max)] (default theta 2-4 deg -> ~[1.0, 4.0] V for
    15 nm hBN). Pass an explicit ``nu1_range`` (V, V) to override.

    ``nu2_lo_factor``/``nu2_hi_factor`` (default 1.8, 2.2): nu=2 is searched per-curve in
    [lo*V1, hi*V1] (enforces V2 ~ 2*V1, rejects nu=3/2 ~1.5*V1). ``nu2_use_shoulder`` (True)
    also accepts a flatness-gated slope-shoulder as nu=2 (not only a local minimum).

    ``cnp_nu2_weight`` (0.5): anchor quality = conf1^(1-w)*conf2^w (combined nu=1 & nu=2).
    ``cnp_top_n_nu2`` (None -> auto = total_points//12): how many best-quality curves set CNP.
    ``smooth_window`` (15): Savitzky-Golay window for dip detection.

    points     : list of (x_array, y_array) gate-sweeps, in grid (row-major) order.
    pts_per_row: grid width (for the spatial-consistency 8-neighbour test).
    """
    n = len(points)
    rows = [i // pts_per_row for i in range(n)]
    cols = [i % pts_per_row for i in range(n)]
    # Gate capacitance and nu=1 window from the twist-angle range (CNP assumed 0).
    a0_m = lattice_constant_angstrom * 1e-10
    C_tg = (EPS0 * epsilon_hbn / (top_hbn_thickness_nm * 1e-9)) if has_top_gate else 0.0
    C_bg = (EPS0 * epsilon_hbn / (bottom_hbn_thickness_nm * 1e-9)) if has_bottom_gate else 0.0
    C_total = C_tg + C_bg
    if nu1_range is None:
        nu1_range = (_v_nu1_for_theta(theta_min, C_total, a0_m),
                     _v_nu1_for_theta(theta_max, C_total, a0_m))
    v1 = np.full(n, np.nan)
    v0 = np.full(n, np.nan)
    v1_src = [""] * n
    all_dips, sig = [], []
    xs, yssig, ysraw, noises = [], [], [], []
    for i, (x, y) in enumerate(points):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        order = np.argsort(x)
        x, y = x[order], y[order]
        xs.append(x)
        ysraw.append(y)
        if x.size < 7 or not np.isfinite(y).all():
            all_dips.append([])
            yssig.append(np.zeros_like(x))
            noises.append(1e-9)
            continue
        dips, ys, _noise = _detect_dips(x, y, smooth=smooth_window)
        all_dips.append(dips)
        yssig.append(ys)
        noises.append(_noise)
        a1, s1 = _pick_mott(x, y, dips, ys, nu1_range)
        v1[i], v1_src[i] = a1, s1
        v0[i] = _pick_cnp(x, ys, dips, nu0_range)

    v1, v1_src = _spatial_consistency(rows, cols, v1, v1_src, all_dips,
                                      hampel_k=hampel_k, hampel_floor_v=hampel_floor_v)

    v2 = np.full(n, np.nan)
    conf2 = np.zeros(n)
    conf1 = np.zeros(n)
    for i in range(n):
        if not np.isfinite(v1[i]):
            continue
        # nu=1 confidence = significance of the detected dip nearest V1.
        near1 = [d for d in all_dips[i] if abs(d.v - v1[i]) <= NU2_GAP]
        if near1:
            conf1[i] = max(near1, key=lambda d: d.significance).significance
        if nu2_lo_factor is not None and nu2_hi_factor is not None:
            lo2, hi2 = nu2_lo_factor * v1[i], nu2_hi_factor * v1[i]   # V2 ~ 2*V1 window
        else:
            lo2, hi2 = v1[i] + NU2_GAP, NU2_MAX_V
        cands = [d for d in all_dips[i] if lo2 <= d.v <= hi2]
        v2v, v2c = np.nan, 0.0
        if cands:
            b = max(cands, key=lambda d: d.significance)
            v2v, v2c = b.v, b.significance
        if nu2_use_shoulder and i < len(xs) and xs[i].size >= 7:
            # also try a shoulder (slope-kink); keep it if more significant than any dip.
            sh = _find_shoulder_nu2(xs[i], ysraw[i], yssig[i], noises[i], lo2, hi2)
            if sh is not None and sh[2] > v2c:
                v2v, v2c = sh[0], sh[2]
        if np.isfinite(v2v) and v2c > 0:
            v2[i], conf2[i] = v2v, v2c

    cnp_pp = 2.0 * v1 - v2
    # Rank the CNP-anchor curves by COMBINED nu=1 & nu=2 confidence (weighted geometric
    # mean, conf1^(1-w) * conf2^w with w=cnp_nu2_weight), so only curves where BOTH integer
    # states are well-resolved set the CNP. w>0.5 puts more weight on the nu=2 dip.
    _w2 = float(cnp_nu2_weight)
    conf_comb = (np.clip(conf1, 0, None) ** (1.0 - _w2)) * (np.clip(conf2, 0, None) ** _w2)
    # How many curves anchor the CNP: None -> auto = total points // 12.
    n_top = (max(1, n // 12) if cnp_top_n_nu2 is None else int(cnp_top_n_nu2))
    valid = np.isfinite(v2) & (conf_comb > 0)
    if n_top > 0 and valid.sum() > 0:
        # CNP from the top-n_top curves by combined confidence (weighted 2*V1 - V2).
        idx = np.where(valid)[0]
        top = idx[np.argsort(conf_comb[idx])[::-1][:n_top]]
        cnp_global = float(np.sum(conf_comb[top] * cnp_pp[top]) / np.sum(conf_comb[top]))
    elif (valid & (conf2 >= CNP_CONF_THRESH)).sum() > 0:
        # Legacy rule (cnp_top_n_nu2=0): every nu=2 above the confidence threshold.
        have = valid & (conf2 >= CNP_CONF_THRESH)
        cnp_global = float(np.sum(conf2[have] * cnp_pp[have]) / np.sum(conf2[have]))
    else:
        cnp_global = float(np.nanmedian(v0)) if np.isfinite(v0).any() else 0.0

    if input_v0 is None:
        cnp_used = np.full(n, cnp_global)
    elif np.isscalar(input_v0):
        cnp_used = np.full(n, float(input_v0))
        cnp_global = float(input_v0)
    else:
        cnp_used = np.asarray(input_v0, float)

    # C_total and a0_m already computed at the top (also used for the nu=1 theta window).
    theta = np.zeros(n)
    spacing = v1 - cnp_used
    for i in range(n):
        if not np.isfinite(spacing[i]) or spacing[i] <= 0 or C_total <= 0:
            continue
        n_s = C_total * spacing[i] / E
        a_M = np.sqrt(2.0 / (np.sqrt(3.0) * n_s))
        s = a0_m / (2.0 * a_M)
        if abs(s) > 1.0:
            continue
        theta[i] = float(np.degrees(2.0 * np.arcsin(s)))

    return {
        "theta": theta, "v1": v1, "v2": v2, "conf2": conf2, "conf1": conf1,
        "conf_comb": conf_comb, "v0": v0, "cnp_global": cnp_global,
        "v1_source": v1_src, "xs": xs, "smooths": yssig, "all_dips": all_dips,
    }


# ---------------------------------------------------------------------------
# GWC / Mott strength + best measurement points
# ---------------------------------------------------------------------------
CLOSE_FILL = 0.14                # morphological-closing scale (in filling)
FRAC_WINDOW = (0.10, 0.90)       # default fractional GWC integration window (in nu)
MOTT_WINDOW = (0.85, 1.15)       # nu=1 integration window (in nu)


def _mott_depth_mV(x, ys, dips, v1, v0):
    """Shoulder-baseline depth (mV) of the Mott dip, matching integer_strength:
    window half-width = DEPTH_HALF_WIDTH_FILL * (V1 - V0_per-curve); snap to the
    nearest detected dip inside that window, else use the anchor."""
    slope_pc = v1 - v0
    if not np.isfinite(slope_pc) or slope_pc <= 0:
        return 0.0
    hw = DEPTH_HALF_WIDTH_FILL * slope_pc
    near = [d for d in dips if abs(d.v - v1) <= hw]
    center = min(near, key=lambda d: abs(d.v - v1)).v if near else v1
    return _shoulder_depth(x, ys, center, hw) * 1e3


def _frac_weight(x, ys, v1, cnp, lo, hi, close_fill):
    """Integrated GWC weight: area of the closing-deficit over nu in [lo, hi]."""
    slope = v1 - cnp
    if not np.isfinite(slope) or slope <= 0 or x.size < 7:
        return np.nan
    nu = (x - cnp) / slope
    dx = float(np.median(np.diff(x)))
    y_mV = ys * 1e3
    k = max(int(round(close_fill * slope / dx)), 5)
    deficit = np.clip(ndi.grey_closing(y_mV, size=k, mode="nearest") - y_mV, 0.0, None)
    m = (nu >= lo) & (nu <= hi)
    if m.sum() < 3:
        return np.nan
    o = np.argsort(nu[m])
    return float(np.trapezoid(deficit[m][o], nu[m][o]))


def compute_best_points(
    points,
    pts_per_row,
    *,
    has_top_gate=False,
    has_bottom_gate=True,
    top_hbn_thickness_nm=15.0,
    bottom_hbn_thickness_nm=15.0,
    epsilon_hbn=3.5,
    lattice_constant_angstrom=3.29,
    nu0_range=(-0.98, 0.20),
    nu1_range=None,
    theta_min=2.0,
    theta_max=4.0,
    smooth_window=15,
    hampel_k=SPATIAL_HAMPEL_K,
    hampel_floor_v=SPATIAL_HAMPEL_FLOOR_V,
    input_v0=None,
    fractional_window=FRAC_WINDOW,
    mott_window=MOTT_WINDOW,
    close_fill=CLOSE_FILL,
    domain_split=V1_DOMAIN_SPLIT,
    n_best=3,
):
    """Rank grid points by combined GWC + Mott strength.

    score = sqrt( (Mott depth / max Mott) * (fractional GWC weight / max GWC) ),
    evaluated only in the high-twist domain (V1 >= domain_split), where the fractional
    GWC states exist. Returns the top ``n_best`` points plus per-point arrays.
    """
    res = compute_twist_disorder(
        points, pts_per_row,
        has_top_gate=has_top_gate, has_bottom_gate=has_bottom_gate,
        top_hbn_thickness_nm=top_hbn_thickness_nm,
        bottom_hbn_thickness_nm=bottom_hbn_thickness_nm,
        epsilon_hbn=epsilon_hbn, lattice_constant_angstrom=lattice_constant_angstrom,
        nu0_range=nu0_range, nu1_range=nu1_range, theta_min=theta_min, theta_max=theta_max,
        smooth_window=smooth_window, hampel_k=hampel_k, hampel_floor_v=hampel_floor_v,
        input_v0=input_v0,
    )
    n = len(points)
    v1, v0, cnp, theta = res["v1"], res["v0"], res["cnp_global"], res["theta"]
    xs, smooths, all_dips = res["xs"], res["smooths"], res["all_dips"]
    rows = [i // pts_per_row for i in range(n)]
    cols = [i % pts_per_row for i in range(n)]

    mott_depth = np.zeros(n)
    frac_w = np.full(n, np.nan)
    mott_w = np.full(n, np.nan)
    for i in range(n):
        if not np.isfinite(v1[i]) or xs[i].size < 7:
            continue
        mott_depth[i] = _mott_depth_mV(xs[i], smooths[i], all_dips[i], v1[i], v0[i])
        frac_w[i] = _frac_weight(xs[i], smooths[i], v1[i], cnp, *fractional_window, close_fill)
        mott_w[i] = _frac_weight(xs[i], smooths[i], v1[i], cnp, *mott_window, close_fill)

    high = v1 >= domain_split
    score = np.full(n, np.nan)
    if high.any():
        mmax = np.nanmax(mott_depth[high]) if np.isfinite(mott_depth[high]).any() else 0.0
        fmax = np.nanmax(frac_w[high]) if np.isfinite(frac_w[high]).any() else 0.0
        if mmax > 0 and fmax > 0:
            nM = mott_depth / mmax
            nF = frac_w / fmax
            score = np.where(high, np.sqrt(np.clip(nM, 0, None) * np.clip(nF, 0, None)), np.nan)

    order = np.argsort(-np.where(np.isfinite(score), score, -1.0))
    best = []
    for rk, i in enumerate(order, 1):
        if rk > n_best or not np.isfinite(score[i]):
            break
        best.append({
            "rank": rk, "index": int(i), "row": int(rows[i]), "col": int(cols[i]),
            "twist_deg": float(theta[i]), "mott_depth_mV": float(mott_depth[i]),
            "frac_weight": float(frac_w[i]), "score": float(score[i]),
        })

    return {
        "best": best, "rows": rows, "cols": cols,
        "theta": theta, "v1": v1, "v2": res["v2"], "cnp_global": cnp,
        "mott_depth": mott_depth, "frac_weight": frac_w, "mott_weight": mott_w,
        "score": score, "v1_source": res["v1_source"],
    }


# ---------------------------------------------------------------------------
# Fractional-GWC feature scoring (N_dips-first ranking)
# ---------------------------------------------------------------------------
FRAC_GWC_WINDOW = (0.10, 0.90)   # filling window that isolates the fractional GWC states
FRAC_MERGE_V = 0.18              # merge fractional dips closer than this (V)


def _detect_fractional_dips(x, ys, y_raw, noise, lo, hi, max_n=3, merge_v=FRAC_MERGE_V):
    """Detect fractional GWC dips inside [lo, hi]. Unlike ``_detect_dips`` this applies NO
    topographic-prominence gate: GWC dips sit on a curving background and can have low
    prominence yet a real shoulder-baseline depth. It keeps local minima AND flatness-gated
    slope-shoulders whose shoulder depth clears the significance gate, merges near-duplicates
    (keeping the deeper), and caps at the ``max_n`` deepest. Returns [(v, depth, sig), ...]."""
    hw = DEPTH_HALF_WIDTH_FILL * 3.3
    thr = max(SIGNIFICANCE_NOISE * noise, ABS_DEPTH_FLOOR_V)
    cands = []
    for p in find_peaks(-ys, distance=DIST_PTS)[0]:
        if lo <= x[p] <= hi:
            vc = _parabolic_center(x, ys, int(p))
            d = _shoulder_depth(x, ys, vc, hw)
            if d >= thr:
                cands.append((float(vc), float(d)))
    # flatness-gated slope-shoulders (GWC states can appear as shoulders, not minima)
    dx = float(np.median(np.diff(x)))
    win = _odd_window(len(y_raw), MOTT_STEP_SLOPE_WIN)
    g1 = savgol_filter(y_raw, win, POLYORDER, deriv=1, delta=dx)
    m = (x >= lo) & (x <= hi)
    gmax = float(np.max(g1[m])) if m.any() else 0.0
    for p in find_peaks(-g1, distance=DIST_PTS)[0]:
        if lo <= x[p] <= hi and not (gmax > 0 and g1[p] > SHOULDER_FLAT_FRAC * gmax):
            vc = _parabolic_center(x, ys, int(p))
            d = _shoulder_depth(x, ys, vc, hw)
            if d >= thr:
                cands.append((float(vc), float(d)))
    cands.sort()
    merged = []
    for v, d in cands:
        if merged and abs(v - merged[-1][0]) < merge_v:
            if d > merged[-1][1]:
                merged[-1] = [v, d]
        else:
            merged.append([v, d])
    merged.sort(key=lambda t: -t[1])
    return sorted([(v, d, d / noise) for v, d in merged[:max_n]], key=lambda t: t[0])


def _minmax(a):
    """Min-max normalize to [0, 1] over finite entries (non-finite -> 0)."""
    a = np.asarray(a, dtype=float)
    fin = np.isfinite(a)
    out = np.zeros_like(a)
    if not fin.any():
        return out
    lo, hi = float(np.min(a[fin])), float(np.max(a[fin]))
    out[fin] = (a[fin] - lo) / (hi - lo) if hi > lo else 0.0
    return out


def compute_fractional_gwc_score(
    points,
    pts_per_row,
    *,
    has_top_gate=False,
    has_bottom_gate=True,
    top_hbn_thickness_nm=15.0,
    bottom_hbn_thickness_nm=15.0,
    epsilon_hbn=3.5,
    lattice_constant_angstrom=3.29,
    nu0_range=(-0.98, 0.20),
    nu1_range=None,
    theta_min=2.0,
    theta_max=4.0,
    smooth_window=15,
    hampel_k=SPATIAL_HAMPEL_K,
    hampel_floor_v=SPATIAL_HAMPEL_FLOOR_V,
    input_v0=None,
    cnp_top_n_nu2=None,
    frac_window=FRAC_GWC_WINDOW,
    weights=(1.0, 0.3, 0.0, 1.0, 0.0, 1.0),   # (N, depth, sharp, noise, screening, fill_dev)
    ndips_dominant=True,
    max_gwc_dips=3,
    frac_merge_v=FRAC_MERGE_V,
    n_best=3,
):
    """Rank grid points by fractional-GWC feature richness.

    Looks ONLY inside the fractional-filling window (nu in ``frac_window``, i.e. gate
    voltage strictly between the CNP and the nu=1 Mott), where the generalized-Wigner-
    crystal (GWC) states live. Per point, on the lightly smoothed signal (same detection
    as ``compute_twist_disorder``) it measures:

      * N_dips     -- number of significance-gated dips in the fractional window (the GWC
                      states themselves; nu=0, nu=1 and nu=2 fall outside and are excluded).
      * <depth>    -- mean shoulder-baseline depth of those dips (V).
      * <sharpness>-- mean dip curvature (2nd-derivative term).
      * noise      -- per-curve MAD noise (penalized).
      * screening  -- mean |signal| level, a contact/screening proxy (penalized; screened
                      / contact spots are flat and sit at high offset).

    Each feature is min-max normalized across the grid and combined as
        base = w1*N + w2*depth + w3*sharp - w4*noise - w5*screening.

    With ``ndips_dominant`` True (default) the ranking is strictly dip-count-first: the
    score becomes ``N_dips + tiebreak`` (tiebreak in [0, 1) = normalized ``base``), so a
    point with more GWC dips ALWAYS outranks one with fewer and ``base`` only orders points
    that share the same dip count. With False, ``base`` is used directly.

    CNP mirrors ``compute_twist_disorder``: pass ``input_v0`` to fix it, or set
    ``cnp_top_n_nu2=N`` to define it from the confidence-weighted 2*V1-V2 intercept of the
    N highest-confidence nu=2 curves; otherwise the method's global CNP is used.
    """
    n = len(points)
    rows = [i // pts_per_row for i in range(n)]
    cols = [i % pts_per_row for i in range(n)]

    # 1. nu=1 / nu=2 / CNP from the fixed twist method (respects input_v0).
    res = compute_twist_disorder(
        points, pts_per_row,
        has_top_gate=has_top_gate, has_bottom_gate=has_bottom_gate,
        top_hbn_thickness_nm=top_hbn_thickness_nm,
        bottom_hbn_thickness_nm=bottom_hbn_thickness_nm,
        epsilon_hbn=epsilon_hbn, lattice_constant_angstrom=lattice_constant_angstrom,
        nu0_range=nu0_range, nu1_range=nu1_range, theta_min=theta_min, theta_max=theta_max,
        smooth_window=smooth_window, hampel_k=hampel_k, hampel_floor_v=hampel_floor_v,
        input_v0=input_v0,
    )
    v1 = np.asarray(res["v1"], dtype=float)
    v2 = np.asarray(res["v2"], dtype=float)
    conf2 = np.asarray(res["conf2"], dtype=float)

    # 2. Resolve the CNP.
    if input_v0 is not None:
        cnp = float(input_v0) if np.isscalar(input_v0) else float(np.nanmedian(input_v0))
    elif cnp_top_n_nu2:
        have = np.where(np.isfinite(v2) & (conf2 > 0))[0]
        top = have[np.argsort(conf2[have])[::-1][:int(cnp_top_n_nu2)]]
        intercept = 2.0 * v1 - v2
        cnp = (float(np.sum(conf2[top] * intercept[top]) / np.sum(conf2[top]))
               if top.size else float(res["cnp_global"]))
    else:
        cnp = float(res["cnp_global"])

    # 3. Per-point fractional-window features.
    flo, fhi = frac_window
    Nd = np.zeros(n)
    depth = np.zeros(n)
    sharp = np.zeros(n)
    noise = np.full(n, np.nan)
    level = np.full(n, np.nan)
    fill_dev = np.full(n, np.nan)   # mean |nu - canonical(1/3,1/2,2/3)|: low = balanced GWC ladder
    frac_dips = [[] for _ in range(n)]
    max_dips = int(max_gwc_dips)   # cap ~ GWC states expected in nu in [0.1,0.9] (1/3,1/2,2/3)
    for i, (x, y) in enumerate(points):
        x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
        if x.size < 7 or not np.isfinite(y).all():
            continue
        order = np.argsort(x)
        xs_ = x[order]; y_raw = y[order]
        ys, nz = _smooth_and_noise(y_raw, smooth_window)
        noise[i] = nz
        level[i] = float(np.mean(y))
        if not np.isfinite(v1[i]) or v1[i] <= cnp:
            continue
        vlo = cnp + flo * (v1[i] - cnp)
        vhi = cnp + fhi * (v1[i] - cnp)
        fd = _detect_fractional_dips(xs_, ys, y_raw, nz, vlo, vhi, max_n=max_dips, merge_v=frac_merge_v)
        Nd[i] = len(fd)
        if fd:
            curv = _curvature(y_raw, float(np.median(np.diff(xs_))))
            depth[i] = float(np.mean([d for _v, d, _s in fd]))
            sharp[i] = float(np.mean([max(curv[int(np.argmin(np.abs(xs_ - _v)))], 0.0) for _v, d, _s in fd]))
            frac_dips[i] = [{"v": float(_v), "nu": float((_v - cnp) / (v1[i] - cnp)),
                             "depth_mV": float(d * 1e3), "significance": float(_s)}
                            for _v, d, _s in fd]
            nus = np.array(sorted((_v - cnp) / (v1[i] - cnp) for _v, d, _s in fd))
            if nus.size == 3:   # deviation of the ladder from the ideal 1/3, 1/2, 2/3
                fill_dev[i] = float(np.mean(np.abs(nus - np.array([1.0 / 3, 1.0 / 2, 2.0 / 3]))))

    # 4. Composite score. Default emphasises a clean, low-noise, canonically-spaced GWC
    #    ladder (low noise + balance) over raw dip depth.
    w1, w2, w3, w4, w5, w6 = weights
    base = (w1 * _minmax(Nd) + w2 * _minmax(depth) + w3 * _minmax(sharp)
            - w4 * _minmax(noise) - w5 * _minmax(level) - w6 * _minmax(fill_dev))
    if ndips_dominant:
        score = Nd + 0.999 * _minmax(base)   # integer dip count dominates; base breaks ties
    else:
        score = base

    order = list(np.argsort(-score))
    best = []
    for rk, i in enumerate(order[:n_best], 1):
        best.append({
            "rank": rk, "index": int(i), "row": int(rows[i]), "col": int(cols[i]),
            "n_dips": int(Nd[i]), "mean_depth_mV": float(depth[i] * 1e3),
            "noise_mV": float(noise[i] * 1e3), "score": float(score[i]),
            "twist_deg": float(res["theta"][i]), "frac_dips": frac_dips[i],
        })

    return {
        "best": best, "rows": rows, "cols": cols, "cnp_global": cnp,
        "n_dips": Nd, "mean_depth": depth, "sharpness": sharp,
        "noise": noise, "level": level, "score": score, "v1": v1,
        "frac_dips": frac_dips, "ndips_dominant": bool(ndips_dominant),
    }
