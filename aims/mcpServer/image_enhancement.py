"""
General, parametric contrast enhancement for MIM marker scans.

Designed to run *after* GP regression as a drop-in preprocessing step, and to
generalize to any image without per-scan hand tuning. Two stages:

  1) Robust percentile stretch  -- clip intensities to [p_lo, p_hi] percentiles
     and rescale to [0, 1]. Suppresses bright/dark outliers (e.g. a dirt speck)
     that would otherwise dominate downstream peak-to-peak normalization and
     squash the faint marker features.

  2) CLAHE (Contrast-Limited Adaptive Histogram Equalization) -- boosts *local*
     contrast of the faint arrows, with a clip limit to avoid over-amplifying
     noise in flat regions.
"""
from __future__ import annotations
import numpy as np
from skimage.color import rgb2gray
from skimage.exposure import equalize_adapthist


def enhance_contrast(image, p_lo: float = 2.0, p_hi: float = 99.0, clip_limit: float = 0.01) -> np.ndarray:
    """Enhance contrast of a (grayscale or RGB, uint8 or float) image.

    Args:
        image:      2D/3D array. RGB is converted to gray; uint8 is scaled to [0,1].
        p_lo, p_hi: low/high percentiles for the robust stretch (outlier suppression).
        clip_limit: CLAHE contrast-limit (higher -> stronger local contrast).

    Returns:
        float32 image in [0, 1].
    """
    img = np.asarray(image)
    if img.ndim == 3:
        img = rgb2gray(img)
    g = img.astype(np.float32)
    if g.max() > 1.0:
        g = g / 255.0

    # 1) robust percentile stretch (dirt / outlier suppression)
    lo, hi = np.percentile(g, [p_lo, p_hi])
    g = np.clip((g - lo) / (hi - lo + 1e-9), 0.0, 1.0)

    # 2) CLAHE (local contrast enhancement of the arrows)
    g = equalize_adapthist(g, clip_limit=clip_limit)
    return g.astype(np.float32)
