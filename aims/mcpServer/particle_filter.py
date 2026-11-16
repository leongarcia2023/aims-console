#!/usr/bin/env python3
"""
================================================================================
Title:       MIM Particle Filter
Author:      Philip David Suh
Create Date: 2025/10/04
Institution: Stanford University, Department of Physics
================================================================================

Template-matching particle filter that localizes a scanned image tile within a
larger fiducial-marked template image, reporting position, covariance, and
confidence in physical Cartesian units (x right, y up).
"""
from __future__ import annotations
import os, argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from skimage import io as skio
from skimage.color import rgb2gray
from skimage.transform import rescale
from skimage.feature import match_template
from scipy.ndimage import sobel, uniform_filter1d, gaussian_filter

# Get the directory where this script is located
_SCRIPT_DIR = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class Config:
    """Configuration for a particle-filter localization run.

    Groups the user-facing calibration and performance settings together with
    the internal algorithm parameters (preprocessing, scoring weights,
    refinement, and visualization).
    """
    # === USER-CONFIGURABLE PARAMETERS ===
    
    # Template image path (now relative to script location, not working directory)
    large_image_path: str = str(_SCRIPT_DIR / 'templates' / 'ArrowMarker_10x.jpg')
    
    # Physical calibration (most important parameters)
    # These are the REAL PHYSICAL SIZES, not pixels or ratios
    
    Z_HEIGHT: float = 952.54         # Physical height of TEMPLATE image in micrometers
    SCAN_SIZE_UM: float = 50.0       # Physical size of INPUT/SCAN images in micrometers
                                     # Example: if your microscope scans a 50x50 um area, use 50.0
                                     # The code will automatically rescale to match template scale
    
    # Algorithm performance vs. accuracy tradeoff
    NUM_PARTS: int = 7500          # Number of particles (higher = more accurate, slower)
    NUM_ITERS: int = 25            # Number of iterations (higher = more accurate, slower)
    
    # Output options
    SAVE_VIZ: bool = False         # Save visualization images
    VERBOSE: bool = True           # Print progress information
    
    # === INTERNAL PARAMETERS (Advanced users only) ===
    # These are algorithm implementation details - usually don't need to change
    
    # Preprocessing
    APPLY_ROW_NOISE_SMALL: bool = True
    ROW_DETRENDED: bool = True
    ROW_WIN: int = 51
    HP_SIGMA_TPL: float = 2.0
    HP_SIGMA_BIG: float = 2.0
    
    # Particle filter algorithm
    STEP_PX: float = 1.0
    SOFTMAX_GAIN: float = 20.0
    STRUCT_Q_BIG: float = 0.55
    MIN_STRUCT_FRAC: float = 0.25
    
    # Scoring weights
    W_ZNCC_HP: float = 0.55
    W_HOG: float = 0.35
    W_ZNCC_MAG: float = 0.10
    HOG_BINS: int = 16
    
    # Refinement
    REFINE_RADIUS: int = 8
    REFINE_STEP: int = 1
    
    # Visualization
    DRAW_PHYSICAL_BOX: bool = True
    SAVE_PARTICLE_VIZ: bool = False
    OUT_ROOT: str = 'rl_out'


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class SingleImagePose:
    """Localization result for one image: mean position, covariance, and confidence."""
    mean_xy: np.ndarray    # (2,) in physical units (x right, y up)
    cov_xy: np.ndarray     # (2,2) in physical units^2
    conf: float            # [0,1]

# ---------------------------------------------------------------------------
# Image helpers and feature ops
# ---------------------------------------------------------------------------

def _load_gray(path: str) -> Optional[np.ndarray]:
    """Load an image from *path* as a float32 grayscale array; return None on failure."""
    try:
        img = skio.imread(path)
        if img.ndim == 3:
            img = rgb2gray(img)
        return img.astype(np.float32)
    except Exception as e:
        print(f"Error loading {path}: {e}")
        return None


def row_noise(img: np.ndarray, detrend: bool = False, win: int = 51) -> np.ndarray:
    """Remove the per-row median (with optional row detrending) and rescale to [0, 1]."""
    r = np.median(img, axis=1, keepdims=True)
    out = img - r
    if detrend:
        out = out - uniform_filter1d(out, size=win, axis=1, mode='nearest')
    out = out - out.min()
    return out / (np.ptp(out) + 1e-9)


def highpass(img: np.ndarray, s: float) -> np.ndarray:
    """Return the high-pass image (original minus a Gaussian blur of width *s*)."""
    return img - gaussian_filter(img, s) if s > 0 else img


def grad_mag(img: np.ndarray) -> np.ndarray:
    """Return the Sobel gradient magnitude of *img*."""
    gx = sobel(img, axis=1)
    gy = sobel(img, axis=0)
    return np.hypot(gx, gy).astype(np.float32)


def grad_ori_unsigned(img: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return unsigned gradient orientation (0..pi) and magnitude via Sobel filters."""
    gx = sobel(img, axis=1)
    gy = sobel(img, axis=0)
    ori = (np.arctan2(np.abs(gy), np.abs(gx))).astype(np.float32)  # 0..π
    mag = np.hypot(gx, gy).astype(np.float32)
    return ori, mag


def hist_unsigned(ori: np.ndarray, mag: np.ndarray, bins: int = 16) -> np.ndarray:
    """Return an L2-normalized, magnitude-weighted histogram of unsigned orientations."""
    if ori.size == 0:
        return np.zeros(bins, dtype=np.float32)
    edges = np.linspace(0, np.pi, bins + 1, endpoint=True)
    h, _ = np.histogram(ori.ravel(), bins=edges, weights=mag.ravel())
    n = np.linalg.norm(h) + 1e-9
    return (h / n).astype(np.float32)


def zncc_normed_patch(patch: np.ndarray, tpl_norm: np.ndarray) -> float:
    """Zero-normalized cross-correlation of *patch* against a pre-normalized template."""
    pm, ps = patch.mean(), patch.std() + 1e-6
    return float(np.mean(((patch - pm) / ps) * tpl_norm))


def softmax(x: np.ndarray, g: float) -> np.ndarray:
    """Numerically stable softmax of *x* with gain *g*."""
    x = np.asarray(x, np.float64)
    m = np.nanmax(x)
    z = np.exp(np.clip(g * (x - m), -700, 700))
    z = np.where(np.isfinite(z), z, 0.0)
    s = z.sum()
    return z / s if s > 0 else np.ones_like(z) / len(z)


def resample(parts: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Multinomial resampling of particles *parts* according to weights *w*."""
    w = np.asarray(w, np.float64)
    w = np.where(np.isfinite(w), w, 0.0)
    s = w.sum()
    w = (np.ones_like(w) / len(w)) if s <= 0 else (w / s)
    idx = np.random.choice(len(parts), size=len(parts), p=w)
    return parts[idx]

# ---------------------------------------------------------------------------
# Big image context
# ---------------------------------------------------------------------------
@dataclass
class BigContext:
    """Preprocessed template image plus its physical (pixel-to-unit) calibration."""
    big_gray: np.ndarray
    big_rgb: np.ndarray
    big_hp: np.ndarray
    big_mag: np.ndarray
    big_struct_mask: np.ndarray
    H: int
    W: int
    CX: float
    CY: float
    px_per_unit_big: float   # pixels per physical unit
    origin_x_px: float        # Origin X coordinate in pixels
    origin_y_px: float        # Origin Y coordinate in pixels


def build_big_context(cfg: Config, origin_x_px: Optional[float] = None, origin_y_px: Optional[float] = None) -> BigContext:
    """Load the template ("big") image and precompute its features and calibration.

    Args:
        cfg: Run configuration (supplies the template path and filter widths).
        origin_x_px, origin_y_px: Optional pixel origin; default to the image center.

    Returns:
        A populated BigContext.
    """
    big_gray = _load_gray(cfg.large_image_path)
    if big_gray is None:
        raise FileNotFoundError(f"Cannot load big image: {cfg.large_image_path}")
    big_rgb = skio.imread(cfg.large_image_path)
    H, W = big_gray.shape
    CX, CY = W / 2.0, H / 2.0

    # Use provided origin or default to image center
    if origin_x_px is None:
        origin_x_px = CX
    if origin_y_px is None:
        origin_y_px = CY

    # physical px/unit on big image
    # Use Z_HEIGHT for calibration (simplified)
    px_per_unit_big = H / float(cfg.Z_HEIGHT)

    big_hp = highpass(big_gray, cfg.HP_SIGMA_BIG)
    big_mag = grad_mag(big_gray)
    tau_struct = np.quantile(big_mag, cfg.STRUCT_Q_BIG)
    big_struct_mask = big_mag >= tau_struct

    return BigContext(big_gray, big_rgb, big_hp, big_mag, big_struct_mask, H, W, CX, CY, px_per_unit_big, origin_x_px, origin_y_px)

# ---------------------------------------------------------------------------
# Particle Visualization
# ---------------------------------------------------------------------------

def save_particle_visualization(iteration: int, parts: np.ndarray, weights: np.ndarray, 
                               ctx: BigContext, tpl: np.ndarray, tw: float, th: float,
                               output_dir: Optional[Path] = None) -> None:
    """Save particle visualization at a specific iteration."""
    from pathlib import Path
    
    # Create particles directory if it doesn't exist
    if output_dir is not None:
        particles_dir = output_dir / "particles_viz"
    else:
        particles_dir = Path("particles_viz")
    particles_dir.mkdir(exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(ctx.W/100, ctx.H/100), dpi=100)
    ax.imshow(ctx.big_rgb, interpolation='nearest')
    ax.axis('off')
    
    # Color particles by weight
    colors = plt.cm.plasma(weights / (weights.max() + 1e-12))
    ax.scatter(parts[:, 0], parts[:, 1], c=colors, s=3, alpha=0.7)
    
    # Highlight top 10% particles
    top_indices = np.argsort(weights)[-len(weights)//10:]
    ax.scatter(parts[top_indices, 0], parts[top_indices, 1], 
               c='red', s=8, marker='x', alpha=0.9, linewidths=1)
    
    # Show template bounds
    weighted_mean = np.average(parts, axis=0, weights=weights)
    x_center, y_center = weighted_mean[0], weighted_mean[1]
    rect = plt.Rectangle((x_center - tw/2, y_center - th/2), tw, th, 
                        linewidth=2, edgecolor='lime', facecolor='none', alpha=0.8)
    ax.add_patch(rect)
    
    # Add iteration info
    ax.text(20, 50, f'Iteration {iteration}', fontsize=12, color='white', 
            bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))
    ax.text(20, 80, f'Particles: {len(parts)}', fontsize=10, color='white',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))
    
    plt.tight_layout()
    plt.savefig(particles_dir / f"particles_iter_{iteration:03d}.png", 
                dpi=100, bbox_inches='tight')
    plt.close(fig)

# ---------------------------------------------------------------------------
# Single-image particle filter to physical measurement
# ---------------------------------------------------------------------------

def preprocess_small(cfg: Config, small_raw: np.ndarray) -> np.ndarray:
    """Apply optional row-noise removal to a raw scan image before matching."""
    if cfg.APPLY_ROW_NOISE_SMALL:
        return row_noise(small_raw, detrend=cfg.ROW_DETRENDED, win=cfg.ROW_WIN)
    return small_raw


def rescale_to_physical(scan_size_um: float, ctx: BigContext, small: np.ndarray, small_raw: np.ndarray) -> np.ndarray:
    """
    Rescale scan/input image to match template's physical scale.
    
    Args:
        scan_size_um: Real physical size of input image in micrometers
        ctx: Template context with px_per_unit_big (template's px/um scale)
        small: Preprocessed input image
        small_raw: Original input image (for size reference)
    
    Returns:
        Rescaled image matching template's physical scale
        
    Math:
        - Input image: H pixels representing scan_size_um micrometers
        - Input scale: H / scan_size_um px/um
        - Template scale: ctx.px_per_unit_big px/um
        - Scale factor = template_scale / input_scale
        - Scale factor = ctx.px_per_unit_big / (H / scan_size_um)
    """
    input_height_pixels = small_raw.shape[0]
    input_scale_px_per_um = input_height_pixels / scan_size_um
    template_scale_px_per_um = ctx.px_per_unit_big
    
    scale_factor = template_scale_px_per_um / input_scale_px_per_um
    
    if abs(scale_factor - 1.0) < 1e-6:
        return small  # Already at correct scale
    
    return rescale(small, scale_factor, anti_aliasing=False, preserve_range=True)


def localize_one_image_on_big(cfg: Config, ctx: BigContext, tpl: np.ndarray, 
                              small_image: Optional[np.ndarray] = None):
    """Run PF on one tile template; return SingleImagePose (in physical units) and viz info."""
    H, W = ctx.H, ctx.W

    th, tw = tpl.shape
    if th < 8 or tw < 8:
        raise ValueError(f"Template too small after scaling: {th}x{tw}")

    # Template features
    tpl_hp = highpass(tpl, cfg.HP_SIGMA_TPL)
    thp_m, thp_s = tpl_hp.mean(), tpl_hp.std() + 1e-6
    tpl_hp_norm = (tpl_hp - thp_m) / thp_s

    tpl_ori, tpl_omag = grad_ori_unsigned(tpl)
    tpl_hist = hist_unsigned(tpl_ori, tpl_omag, bins=cfg.HOG_BINS)
    if cfg.W_ZNCC_MAG > 0:
        tm_m, tm_s = tpl_omag.mean(), tpl_omag.std() + 1e-6
        tpl_mag_norm = (tpl_omag - tm_m) / tm_s

    # Seeds (top-K NCC on high-pass)
    # Internal seeding parameters
    SEED_TOPK = 10
    SEED_STD_PX = 12.0
    
    corr = match_template(ctx.big_hp, tpl_hp, pad_input=True)
    flat = corr.ravel()
    topk = np.argpartition(flat, -SEED_TOPK)[-SEED_TOPK:]
    seeds = []
    for idx in topk:
        ypk, xpk = np.unravel_index(idx, corr.shape)
        seeds.append((xpk + tw / 2.0, ypk + th / 2.0))
    seeds = np.array(seeds, dtype=np.float32) if len(seeds) else np.zeros((0,2), np.float32)

    # Bounds so full window stays in-bounds
    X_MIN, X_MAX = tw / 2.0, W - tw / 2.0
    Y_MIN, Y_MAX = th / 2.0, H - th / 2.0

    # Init particles: clustered around seeds (80%) + global (20%)
    parts_list = []
    n_local_each = max(1, int(0.8 * cfg.NUM_PARTS / max(1, len(seeds))))
    for xs, ys in seeds:
        pl = np.zeros((n_local_each, 2), np.float32)
        pl[:, 0] = np.random.normal(np.clip(xs, X_MIN, X_MAX), SEED_STD_PX, n_local_each)
        pl[:, 1] = np.random.normal(np.clip(ys, Y_MIN, Y_MAX), SEED_STD_PX, n_local_each)
        parts_list.append(pl)
    n_local = n_local_each * (len(seeds) if len(seeds) else 1)
    n_global = max(0, cfg.NUM_PARTS - n_local)
    if n_global > 0:
        pg = np.zeros((n_global, 2), np.float32)
        pg[:, 0] = np.random.uniform(X_MIN, X_MAX, n_global)
        pg[:, 1] = np.random.uniform(Y_MIN, Y_MAX, n_global)
        parts_list.append(pg)
    parts = np.vstack(parts_list) if parts_list else np.zeros((cfg.NUM_PARTS,2), np.float32)
    parts[:, 0] = np.clip(parts[:, 0], X_MIN, X_MAX)
    parts[:, 1] = np.clip(parts[:, 1], Y_MIN, Y_MAX)
    weights = np.ones(parts.shape[0], np.float64) / parts.shape[0]

    # PF iterations (structure-gated)
    for iteration in range(cfg.NUM_ITERS):
        parts[:, 0] += np.random.normal(0, cfg.STEP_PX, size=parts.shape[0])
        parts[:, 1] += np.random.normal(0, cfg.STEP_PX, size=parts.shape[0])
        parts[:, 0] = np.clip(parts[:, 0], X_MIN, X_MAX)
        parts[:, 1] = np.clip(parts[:, 1], Y_MIN, Y_MAX)

        scores = np.empty(parts.shape[0], np.float64)
        for j, (xc, yc) in enumerate(parts):
            x0 = int(round(xc - tw / 2.0)); y0 = int(round(yc - th / 2.0))

            # Structure gate
            struct_win = ctx.big_struct_mask[y0:y0 + th, x0:x0 + tw]
            if struct_win.shape != (th, tw) or struct_win.mean() < cfg.MIN_STRUCT_FRAC:
                scores[j] = -1e3; continue

            # ZNCC on high-pass
            patch_hp = ctx.big_hp[y0:y0 + th, x0:x0 + tw]
            s_hp = zncc_normed_patch(patch_hp, tpl_hp_norm)

            # Unsigned HOG cosine
            owin, mwin = grad_ori_unsigned(ctx.big_gray[y0:y0 + th, x0:x0 + tw])
            hwin = hist_unsigned(owin, mwin, bins=cfg.HOG_BINS)
            s_hog = float(np.dot(hwin, tpl_hist))

            # Optional: gradient-magnitude ZNCC
            if cfg.W_ZNCC_MAG > 0:
                mag_win = ctx.big_mag[y0:y0 + th, x0:x0 + tw]
                s_mag = zncc_normed_patch(mag_win, tpl_mag_norm)
            else:
                s_mag = 0.0

            scores[j] = cfg.W_ZNCC_HP * s_hp + cfg.W_HOG * s_hog + cfg.W_ZNCC_MAG * s_mag

        weights = softmax(scores, cfg.SOFTMAX_GAIN)
        
        # Save particle visualization every 5 iterations
        if cfg.SAVE_PARTICLE_VIZ and (iteration + 1) % 5 == 0:
            save_particle_visualization(iteration + 1, parts, weights, ctx, tpl, tw, th,
                                      getattr(cfg, '_output_dir', None))
        
        parts = resample(parts, weights)

    # Weighted mean & covariance in **pixels**
    pmu_px = np.average(parts, axis=0, weights=weights)  # (xh0, yh0) in px (image coords: y down)
    d_px = parts - pmu_px[None, :]
    w = weights / (weights.sum() + 1e-12)
    cov_px = (d_px.T * w) @ d_px + 1e-9 * np.eye(2)

    # Local refine (dense ZNCC on HP only) — pixel domain
    xh_px, yh_px = float(pmu_px[0]), float(pmu_px[1])
    best = (-1e9, xh_px, yh_px)
    for dy in range(-cfg.REFINE_RADIUS, cfg.REFINE_RADIUS + 1, cfg.REFINE_STEP):
        for dx in range(-cfg.REFINE_RADIUS, cfg.REFINE_RADIUS + 1, cfg.REFINE_STEP):
            xc = np.clip(xh_px + dx, tw/2.0, W - tw/2.0)
            yc = np.clip(yh_px + dy, th/2.0, H - th/2.0)
            x0 = int(round(xc - tw / 2.0)); y0 = int(round(yc - th / 2.0))
            patch_hp = ctx.big_hp[y0:y0 + th, x0:x0 + tw]
            if patch_hp.shape != (th, tw):
                continue
            s = zncc_normed_patch(patch_hp, tpl_hp_norm)
            if s > best[0]:
                best = (s, xc, yc)
    _, xh_px, yh_px = best

    # Confidence from particle distribution (unitless)
    w_sorted = np.sort(w)[::-1]
    margin = float(w_sorted[0] - w_sorted[1]) if len(w) > 1 else 0.0
    Hent = -np.sum(np.where(w > 0, w * np.log(w + 1e-12), 0.0))
    conf = float(1.0 - Hent / np.log(len(w) + 1e-12))
    conf = 0.5 * conf + 0.5 * np.clip(margin * 5.0, 0.0, 1.0)

    # Convert PF output from pixels to physical Cartesian units.
    # Origin: calibrated center of fiducial markers.
    # Coordinate system: x right, y up (standard Cartesian).
    u_per_px = 1.0 / ctx.px_per_unit_big
    x_u = (xh_px - ctx.origin_x_px) * u_per_px
    y_u = -((yh_px - ctx.origin_y_px) * u_per_px)  # Negative for y-up convention
    mean_xy_units = np.array([x_u, y_u], dtype=np.float64)

    # Covariance transform with axis flip: J = diag([u_per_px, -u_per_px])
    J = np.diag([u_per_px, -u_per_px])
    cov_units = J @ cov_px @ J.T
    # Use normal measurement uncertainty from particle filter covariance
    cov_units += 1.0 * np.eye(2)  # Add small measurement uncertainty

    meas = SingleImagePose(mean_xy=mean_xy_units, cov_xy=cov_units, conf=conf)

    # Viz info (still in pixels for drawing)
    x0, y0 = int(round(xh_px - tw / 2.0)), int(round(yh_px - th / 2.0))
    return meas, (xh_px, yh_px, float(tw), float(th), x0, y0)


def predict_single_image(cfg: Config, ctx: BigContext, image_path: str, scan_size_um: float):
    """Load, preprocess, and rescale one scan image, then localize it on the template."""
    small_raw = _load_gray(image_path)
    if small_raw is None:
        raise FileNotFoundError(f"Cannot load tile: {image_path}")
    small = preprocess_small(cfg, small_raw)
    tpl = rescale_to_physical(scan_size_um, ctx, small, small_raw)
    return localize_one_image_on_big(cfg, ctx, tpl, small_image=small)

# ---------------------------------------------------------------------------
# Single Image Processing
# ---------------------------------------------------------------------------

def process_single_image(cfg: Config, image_path: str, output_dir: Path, 
                        save_viz: bool = True, scan_size_um: Optional[float] = None) -> SingleImagePose:
    """Process a single image and return position with optional visualization."""
    output_dir.mkdir(parents=True, exist_ok=True)
    viz_dir = output_dir / 'viz' if save_viz else None
    if viz_dir:
        viz_dir.mkdir(parents=True, exist_ok=True)

    ctx = build_big_context(cfg)
    
    # Use provided scan_size_um or fall back to config default
    if scan_size_um is None:
        scan_size_um = cfg.SCAN_SIZE_UM
    
    print(f"Processing single image: {image_path}")
    
    # Get PF detection for this image
    meas, viz_info = predict_single_image(cfg, ctx, image_path, scan_size_um)
    
    print(f"Detected position: ({meas.mean_xy[0]:.1f}, {meas.mean_xy[1]:.1f}) μm")
    print(f"Confidence: {meas.conf:.3f}")
    print(f"Uncertainty: (±{np.sqrt(meas.cov_xy[0,0]):.1f}, ±{np.sqrt(meas.cov_xy[1,1]):.1f}) μm")
    
    # Create visualization if requested
    if save_viz and viz_info:
        xh_px, yh_px, tw, th, x0i, y0i = viz_info
        
        fig, ax = plt.subplots(figsize=(ctx.W/100, ctx.H/100), dpi=100)
        ax.imshow(ctx.big_rgb, interpolation='nearest')
        ax.axis('off')
        
        # PF detection box
        ax.add_patch(Rectangle((x0i, y0i), tw, th, fill=False, ec='red', lw=2, label='Detected Position'))
        
        # Physical box
        if cfg.DRAW_PHYSICAL_BOX:
            box_px = cfg.Z_HEIGHT * ctx.px_per_unit_big
            ax.add_patch(Rectangle((xh_px - box_px/2, yh_px - box_px/2), box_px, box_px,
                                  fill=False, ec='lime', lw=2, ls='--', label=f'{cfg.Z_HEIGHT}×{cfg.Z_HEIGHT} μm'))
        
        # Position marker
        ax.plot(xh_px, yh_px, 'o', color='yellow', markersize=8, markeredgecolor='black', markeredgewidth=2)
        
        # Add position text
        ax.text(0.02, 0.98, f'Position: ({meas.mean_xy[0]:.1f}, {meas.mean_xy[1]:.1f}) μm', 
                transform=ax.transAxes, fontsize=10, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        
        ax.legend(loc='lower right', fontsize=8, frameon=True)
        ax.set_title('Single Image Localization Result', fontsize=12, fontweight='bold')
        
        fig.tight_layout()
        fig.savefig(str(viz_dir / 'localization_result.png'), dpi=100, bbox_inches='tight')
        plt.close(fig)

        print(f"Visualization saved to: {viz_dir / 'localization_result.png'}")
    
    return meas

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    """Parse command-line arguments for single-image localization."""
    p = argparse.ArgumentParser(description='Single image particle filter localization — physical units (Cartesian)')
    
    p.add_argument('--image', type=str, required=True, help='Path to input image to localize')
    p.add_argument('--template', type=str, default=None, help='Path to template image (overrides Config)')
    p.add_argument('--out', type=str, default='single_image_result', help='Output folder')
    p.add_argument('--save_viz', action='store_true', help='Save visualization overlay')
    p.add_argument('--save_particle_viz', action='store_true', help='Save particle visualization every 5 iterations')
    return p.parse_args()


def main():
    """CLI entry point: localize one image and write the result CSV (and optional viz)."""
    args = parse_args()
    cfg = Config()
    
    # Override template image if provided
    if args.template:
        cfg.large_image_path = args.template
    
    # Enable particle visualization if requested
    if args.save_particle_viz:
        cfg.SAVE_PARTICLE_VIZ = True
    
    # Create output directory
    output_dir = Path(args.out)
    
    # Process single image
    print(f"Single Image Localization Mode:")
    print(f"Input image: {args.image}")
    print(f"Template image: {cfg.large_image_path}")
    print(f"Output folder: {output_dir}")
    print()
        
    # Store output directory in config for particle visualization
    cfg._output_dir = output_dir
    
    # Process the single image
    meas = process_single_image(cfg, args.image, output_dir, save_viz=args.save_viz)
    
    # Save results to CSV
    results_file = output_dir / 'localization_result.csv'
    with open(results_file, 'w', newline='') as f:
        import csv
        writer = csv.writer(f)
        writer.writerow(['x', 'y', 'sx', 'sy', 'confidence'])
        writer.writerow([
            f"{meas.mean_xy[0]:.6f}",
            f"{meas.mean_xy[1]:.6f}", 
            f"{np.sqrt(meas.cov_xy[0,0]):.6f}",
            f"{np.sqrt(meas.cov_xy[1,1]):.6f}",
            f"{meas.conf:.6f}"
        ])
    
    print(f"\nResults saved to: {results_file}")
    if args.save_viz:
        print(f"Visualization saved to: {output_dir / 'viz' / 'localization_result.png'}")


if __name__ == '__main__':
    main()
