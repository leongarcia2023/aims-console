"""
Row-wise polynomial stripe removal for 2D scan images.

Provides ``remove_stripes_row_poly``, which fits and subtracts a low-degree
polynomial background from each row to suppress horizontal scan stripes.
"""
import numpy as np


def remove_stripes_row_poly(img, deg=1, mask=None):
    """
    Remove horizontal stripes using row-wise polynomial fitting.

    Parameters:
    - img: 2D array (ny, nx), any numeric dtype.
    - deg: polynomial degree (1 or 2 usually enough).
    - mask: optional boolean array same shape as img (True = ignore during fit).

    Returns:
    - out: destriped image, same shape and dtype as img (float if needed for subtraction).
    """
    ny, nx = img.shape
    x = np.arange(nx, dtype=np.float64)
    out = np.asarray(img, dtype=np.float64).copy()

    for i in range(ny):
        y = np.asarray(img[i], dtype=np.float64)

        if mask is not None:
            valid = ~mask[i]
            if np.sum(valid) <= deg:
                continue
            coeff = np.polyfit(x[valid], y[valid], deg)
        else:
            coeff = np.polyfit(x, y, deg)

        background = np.polyval(coeff, x)
        out[i] = y - background

    return out

