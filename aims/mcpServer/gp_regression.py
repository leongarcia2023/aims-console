"""
Gaussian-Process regression preprocessing for MIM scans.

This module loads a Microwave Impedance Microscopy scan from an .sxm file,
removes horizontal row stripes, upsamples the rows by an integer factor, and
fills in the missing pixels using a Gaussian Process with a Matern kernel. The
reconstructed image is then normalized, resized to the requested dimensions,
and written out as a PNG. The main entry point is ``run_gp_regression()``.
"""

import os
import numpy as np
import cv2
from pySPM import SXM
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern

from stripe_removal import remove_stripes_row_poly


def reformat_image(img, X_predict, mean):
    """Scatter flat GP predictions back onto a 2D grid.

    Args:
        img: Reference array whose shape defines the output grid.
        X_predict: (N, 2) array of (row, col) pixel coordinates.
        mean: Length-N array of predicted values, one per coordinate.

    Returns:
        2D float array with ``mean`` written at the given coordinates.
    """
    img_mean = np.zeros(shape=img.shape)
    for i in range(len(X_predict)):
        img_mean[X_predict[i, 0], X_predict[i, 1]] = mean[i]
    return img_mean


def run_gp_regression(
    sxm_path: str,
    output_path: str,
    factor: int = 4,
    output_width: int = 256,
    output_height: int = 256,
    channel: str = "MIM-Im_AC-X",
) -> dict:
    """
    Run GP regression on an SXM scan: load channel, detrend, upsample rows by factor,
    fill with Gaussian Process (Matern kernel), then normalize and resize to output dimensions.

    Args:
        sxm_path: Full path to the .sxm file.
        output_path: Full path for the output PNG file.
        factor: Row upsampling factor (default 4).
        output_width: Output image width in pixels (default 256).
        output_height: Output image height in pixels (default 256).
        channel: SXM channel name (default "MIM-Im_AC-X").

    Returns:
        dict: {"success": True, "output_path": str, "width": int, "height": int}
              or {"success": False, "error": str}
    """
    try:
        sxm_path = os.path.abspath(sxm_path)
        output_path = os.path.abspath(output_path)
        if not os.path.isfile(sxm_path):
            return {"success": False, "error": f"SXM file not found: {sxm_path}"}
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        sxm = SXM(sxm_path)
        scan = sxm.get_channel(channel, direction="forward")
        img = scan.pixels

        img = remove_stripes_row_poly(img, deg=1)
        img = (img - img.min()) / (img.max() - img.min()) * 255

        reconstructed_img = np.empty((factor * img.shape[0], img.shape[1]))
        reconstructed_img[:] = np.nan
        for i in range(len(img)):
            for j in range(len(img[0])):
                reconstructed_img[int(i * factor), j] = img[i, j]

        X_train_spr = []
        Y_train_spr = []
        X_predict_spr = []
        for i in range(len(reconstructed_img)):
            for j in range(len(reconstructed_img[0])):
                X_predict_spr.append([i, j])
                if not np.isnan(reconstructed_img[i, j]):
                    Y_train_spr.append(reconstructed_img[i, j])
                    X_train_spr.append([i, j])

        X_train_spr = np.array(X_train_spr)
        Y_train_spr = np.array(Y_train_spr)
        X_predict_spr = np.array(X_predict_spr)

        kernel = 1.0 * Matern(
            length_scale=1.0, length_scale_bounds=(1.0, 256.0), nu=1.5
        )
        gpr = GaussianProcessRegressor(
            kernel=kernel,
            alpha=1e-4,
            n_restarts_optimizer=1,
            normalize_y=False,
        )
        gpr.fit(X_train_spr, Y_train_spr)
        mean_prediction_spr, _ = gpr.predict(X_predict_spr, return_std=True)

        reconstruction = reformat_image(
            reconstructed_img, X_predict_spr, mean_prediction_spr
        )
        reconstruction = np.flipud(reconstruction)
        norm_img = cv2.normalize(reconstruction, None, 0, 255, cv2.NORM_MINMAX)
        norm_img = norm_img.astype("uint8")
        norm_img = cv2.resize(
            norm_img, (output_width, output_height), interpolation=cv2.INTER_LINEAR
        )
        success = cv2.imwrite(output_path, norm_img)
        if not success:
            return {"success": False, "error": f"Failed to write PNG to {output_path}"}
        return {
            "success": True,
            "output_path": output_path,
            "width": output_width,
            "height": output_height,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
