"""
GP regression + contrast enhancement, merged into a single preprocessing pipeline.

Stage 1: GP regression (gp_regression.run_gp_regression) -- detrend, row-upsample, GP fill.
Stage 2: robust percentile stretch + CLAHE (image_enhancement.enhance_contrast) -- suppress
         bright dirt and sharpen faint markers.

Returns a processed PNG ready to feed straight into predict_position.
"""
from __future__ import annotations
import os
import numpy as np
import cv2

from gp_regression import run_gp_regression
from image_enhancement import enhance_contrast


def run_gp_regression_enhanced(
    sxm_path: str,
    output_path: str,
    factor: int = 4,
    output_width: int = 256,
    output_height: int = 256,
    channel: str = "MIM-Im_AC-X",
    p_lo: float = 2.0,
    p_hi: float = 99.0,
    clip_limit: float = 0.01,
) -> dict:
    """Run GP regression then contrast enhancement. See module docstring.

    Writes a single output PNG (``output_path``). The GP intermediate is created
    in a temporary file only so the enhancement stage can read it back, then it is
    always deleted.

    Returns:
        {"success": True, "output_path", "width", "height",
         "enhancement": {...}, "contrast_std_before", "contrast_std_after"}
        or {"success": False, "stage", "error"}.
    """
    gp_path = None
    try:
        output_path = os.path.abspath(output_path)
        base, ext = os.path.splitext(output_path)
        ext = ext or ".png"
        gp_path = base + "_gp" + ext

        # --- Stage 1: GP regression (temporary intermediate) ---
        gp_res = run_gp_regression(
            sxm_path=sxm_path, output_path=gp_path, factor=factor,
            output_width=output_width, output_height=output_height, channel=channel,
        )
        if not gp_res.get("success"):
            return {"success": False, "stage": "gp_regression", "error": gp_res.get("error")}

        gp_img = cv2.imread(gp_path, cv2.IMREAD_GRAYSCALE)
        if gp_img is None:
            return {"success": False, "stage": "load_gp", "error": f"could not read GP image {gp_path}"}

        # --- Stage 2: robust stretch + CLAHE ---
        enh = enhance_contrast(gp_img, p_lo=p_lo, p_hi=p_hi, clip_limit=clip_limit)
        enh_u8 = (np.clip(enh, 0.0, 1.0) * 255.0).astype("uint8")
        if not cv2.imwrite(output_path, enh_u8):
            return {"success": False, "stage": "save", "error": f"failed to write {output_path}"}

        return {
            "success": True,
            "output_path": output_path,
            "width": output_width,
            "height": output_height,
            "enhancement": {"p_lo": p_lo, "p_hi": p_hi, "clip_limit": clip_limit},
            "contrast_std_before": float(gp_img.astype(np.float32).std() / 255.0),
            "contrast_std_after": float(enh.std()),
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}
    finally:
        # The GP intermediate is always a throwaway; never leave it on disk.
        if gp_path is not None:
            try:
                os.remove(gp_path)
            except OSError:
                pass
