"""
Radiometric bookkeeping: decibel handling, look counts, and speckle statistics.

The premise of this package is that a SAR observation of a polygon is an
estimate with a knowable precision rather than a point value. Quantifying that
precision requires three separate quantities, which are routinely conflated.

``looks_nominal``
    The multilooking factor implied by the processing configuration. Geometry,
    not measurement, and known before any data is read. It is an upper bound on
    independence, not an estimate of it.

``enl``
    The equivalent number of looks, estimated from the imagery over
    statistically homogeneous areas. This is the conventional ENL of the SAR
    literature. It is a property of the sensor and the processing chain, not of
    any one polygon, so it is estimated per scene and then applied to every
    polygon in that scene.

``n_effective``
    The number of statistically independent samples a polygon contributes.
    Lower than the pixel count whenever neighboring pixels are correlated,
    which they are after any resampling or interpolation step.

Why the distinction matters
---------------------------
The moment estimator :math:`\\mathrm{ENL} = \\mathrm{E}[I]^2 / \\mathrm{Var}[I]`
is only an estimate of the look count when the sample is statistically
homogeneous. Applied to a whole polygon containing real spatial structure, the
extra variance from that structure depresses the result, so the number measures
a mixture of speckle and texture and is not an ENL in the conventional sense.
Earlier versions of this package reported exactly that quantity under the name
``enl_empirical``, which overstated what it was measuring.

Conversely, interpolation during terrain correction averages neighboring
samples. That genuinely reduces per-pixel variance and so genuinely raises the
per-pixel ENL, but it creates no new information, because the smoothed pixels
are correlated. Reporting a precision of :math:`1/\\sqrt{n L}` from the raised
ENL and the full pixel count therefore double counts: it credits the variance
reduction while ignoring the correlation that produced it. The correct
expression uses the independent sample count,

.. math::

    \\mathrm{SE}_{\\mathrm{rel}} = \\frac{1}{\\sqrt{n_{\\mathrm{eff}} L}},
    \\qquad n_{\\mathrm{eff}} = n / \\kappa,

where :math:`\\kappa = \\sum_{\\mathrm{lags}} \\rho` is the variance inflation
factor of the spatial autocorrelation. Because smoothing raises :math:`L` and
:math:`\\kappa` by about the same factor, :math:`n_{\\mathrm{eff}} L` recovers
the information content the imagery actually carries.

Distributional basis
--------------------
For fully developed speckle, an L-look intensity (linear power) observation of a
homogeneous target with mean backscatter :math:`\\sigma` is Gamma distributed
with shape :math:`L` and scale :math:`\\sigma/L`, so
:math:`\\mathrm{Var}[I] = \\sigma^2 / L` and the coefficient of variation is
:math:`1/\\sqrt{L}`.

With texture, the standard multiplicative model gives

.. math::

    \\mathrm{CV}^2_{\\mathrm{obs}} = \\frac{1}{L}
    + \\mathrm{CV}^2_{\\mathrm{tex}}\\left(1 + \\frac{1}{L}\\right),

which inverts to recover texture once :math:`L` is known. That inversion is
what separates a genuinely heterogeneous target from a noisy measurement of a
uniform one, and it is why the scene ENL has to be estimated independently of
the polygons.

References
----------
Oliver, C. and Quegan, S. (2004). *Understanding Synthetic Aperture Radar
Images*. SciTech Publishing.

Anfinsen, S. N., Doulgeris, A. P. and Eltoft, T. (2009). Estimation of the
equivalent number of looks in polarimetric synthetic aperture radar imagery.
*IEEE Transactions on Geoscience and Remote Sensing*, 47(11), 3795-3809.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

__all__ = [
    "to_db",
    "to_linear",
    "mean_linear_then_db",
    "nominal_looks",
    "estimate_enl",
    "estimate_scene_enl",
    "coefficient_of_variation",
    "texture_cv",
    "aggregate_polygon",
    "autocorrelation_inflation_factor",
    "effective_sample_size",
    "speckle_standard_error_db",
    "gamma_change_statistic",
]

# Sentinel-1 IW single-look geometry, ground range by azimuth, in metres.
#
# Resolution is the width of the impulse response; spacing is the sample
# interval. Range is oversampled (2.3 m spacing within a 2.7 m cell) so
# adjacent range samples are correlated, and azimuth spacing is well inside the
# 22 m azimuth resolution. Using spacing to count looks therefore overstates
# independence, which is why the nominal count below is derived from resolution.
S1_IW_SLC_RESOLUTION_M = (2.7, 22.0)
S1_IW_SLC_PIXEL_SPACING_M = (2.3, 14.1)

# GRDH is delivered already multilooked onto a 10 m grid with a documented
# resolution of about 20 m by 22 m and a widely cited ENL near 4.4.
S1_IW_GRDH_RESOLUTION_M = (20.0, 22.0)
S1_IW_GRDH_PIXEL_SPACING_M = (10.0, 10.0)
S1_IW_GRDH_NOMINAL_ENL = 4.4


def to_db(linear: np.ndarray | float) -> np.ndarray:
    """Convert linear power to decibels, mapping non-positive values to NaN."""
    arr = np.asarray(linear, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 10.0 * np.log10(np.where(arr > 0, arr, np.nan))
    return out


def to_linear(db: np.ndarray | float) -> np.ndarray:
    """Convert decibels to linear power."""
    arr = np.asarray(db, dtype=np.float64)
    return np.power(10.0, arr / 10.0)


def mean_linear_then_db(values, already_db: bool = False) -> float:
    """
    Aggregate backscatter correctly.

    Averaging in the decibel domain is biased low, and the size of the bias
    grows with within-polygon heterogeneity. Since heterogeneity is often the
    quantity of interest, the bias is correlated with the signal and cannot be
    treated as noise. Aggregation therefore always happens in linear power.
    """
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    linear = to_linear(arr) if already_db else arr
    linear = linear[linear > 0]
    if linear.size == 0:
        return float("nan")
    return float(to_db(linear.mean()))


def nominal_looks(
    output_spacing_m: float,
    source: str = "SLC",
    native_resolution_m: tuple[float, float] | None = None,
) -> dict[str, float]:
    """
    Derive the look count implied by the processing configuration.

    This is geometry, not measurement. It reports how many native resolution
    cells fit inside one output pixel, which bounds how many independent looks
    the output pixel could contain. The measured ENL is almost always lower,
    because spectral overlap between looks, range oversampling and
    interpolation all introduce correlation that this ratio cannot see.

    Resolution rather than sample spacing is used deliberately. Sentinel-1 range
    samples are spaced 2.3 m apart within a 2.7 m resolution cell, so counting
    samples would credit 4.3 looks to a 10 m pixel that can hold at most 3.7
    independent range looks. Earlier versions of this package used spacing and
    therefore reported a nominal count about 18 percent too high in range.

    Either axis is floored at one look. An output pixel finer than the native
    resolution is interpolating rather than averaging, which adds correlation
    between neighboring pixels instead of looks within them. That correlation is
    accounted for separately through :func:`effective_sample_size`.
    """
    source = source.upper()
    if source == "SLC":
        native = native_resolution_m or S1_IW_SLC_RESOLUTION_M
        base_looks = 1.0
    elif source.startswith("GRD"):
        native = native_resolution_m or S1_IW_GRDH_RESOLUTION_M
        base_looks = S1_IW_GRDH_NOMINAL_ENL
    else:
        raise ValueError(f"Unknown source product type: {source!r}")

    looks_range = max(1.0, output_spacing_m / native[0])
    looks_azimuth = max(1.0, output_spacing_m / native[1])

    # A pixel finer than the native cell in a given axis is oversampled rather
    # than multilooked. Recording the ratio lets downstream code know how much
    # inter-pixel correlation to expect even without measuring it.
    oversampling_range = max(1.0, native[0] / output_spacing_m)
    oversampling_azimuth = max(1.0, native[1] / output_spacing_m)

    return {
        "looks_range": float(looks_range),
        "looks_azimuth": float(looks_azimuth),
        "looks_nominal": float(base_looks * looks_range * looks_azimuth),
        "oversampling_range": float(oversampling_range),
        "oversampling_azimuth": float(oversampling_azimuth),
        "native_resolution_range_m": float(native[0]),
        "native_resolution_azimuth_m": float(native[1]),
        "output_spacing_m": float(output_spacing_m),
        "source_product": source,
    }


def estimate_enl(
    intensity: np.ndarray,
    already_db: bool = False,
    min_pixels: int = 16,
    trim_percentile: float | None = None,
) -> float:
    """
    Moment estimator of the equivalent number of looks.

    Returns ``mean**2 / var`` computed on linear power, which estimates the look
    count only when the sample is statistically homogeneous. Over a target with
    real spatial structure the extra variance depresses the result, so the
    returned value is a lower bound on the scene ENL and is better read as a
    combined speckle-plus-texture statistic.

    Use :func:`estimate_scene_enl` to estimate the ENL of an image, and
    :func:`texture_cv` to separate texture from speckle once the scene ENL is
    known. This function remains available for homogeneous samples, for
    calibration targets, and for the window estimates that the scene estimator
    is built from.

    Parameters
    ----------
    intensity
        Linear-power pixel values from a statistically homogeneous sample.
    already_db
        Set when the input is in decibels so it can be converted back. The
        estimator is invalid on decibel values, because the log transform is
        variance stabilising and yields a number unrelated to the look count.
    min_pixels
        Below this count the estimator is too unstable to report. Its own
        variance is roughly ``2 L**2 / n``, so at ``n = 16`` the relative
        standard error is already about 35 percent.
    trim_percentile
        Symmetrically trim this percentage from each tail before estimating.
        Guards against isolated bright scatterers such as buildings, metal
        structures or standing-water edges. It does not remove target-scale
        texture, which is what :func:`texture_cv` is for.
    """
    arr = np.asarray(intensity, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if already_db:
        arr = to_linear(arr)
    arr = arr[arr > 0]

    if arr.size < min_pixels:
        return float("nan")

    if trim_percentile:
        lo, hi = np.percentile(arr, [trim_percentile, 100.0 - trim_percentile])
        arr = arr[(arr >= lo) & (arr <= hi)]
        if arr.size < min_pixels:
            return float("nan")

    mean = arr.mean()
    var = arr.var(ddof=1)
    if var <= 0:
        return float("nan")
    return float(mean * mean / var)


def estimate_scene_enl(
    intensity: np.ndarray,
    window: int = 9,
    already_db: bool = False,
    min_windows: int = 30,
    method: str = "homogeneous",
    homogeneity_percentile: float = 5.0,
    bins: int = 120,
) -> dict[str, float]:
    """
    Estimate the ENL of an image from its most homogeneous regions.

    The image is divided into small non-overlapping blocks and the within-block
    variability is measured for each. The asymmetry of the resulting
    distribution is what makes the estimate possible, because texture can only
    add variance. Blocks with the least variability are therefore the
    homogeneous ones, and the ENL of the processing chain is read from those
    rather than from the scene as a whole.

    Averaging over all blocks would be wrong for the same reason. Every textured
    block adds variance, so the result would describe the scene's texture rather
    than the sensor and processor's look count.

    Methods
    -------
    ``homogeneous`` (default)
        Works in squared coefficient of variation rather than in ENL. Takes a
        low percentile of the per-block ``var/mean**2``, corrects it for the
        estimator's own sampling noise, and inverts to an ENL.

        Operating in this space matters more than it sounds. ENL is the
        reciprocal of a noisy variance estimate, and by Jensen's inequality the
        reciprocal of a noisy quantity is biased upward, so selecting on ENL
        directly overestimates. Measured on simulated homogeneous imagery with
        true look counts of 1, 4.4, 12 and 40, selecting in ENL space returned
        values 3 to 23 percent high depending on window size, while this method
        stayed within 1.4 percent for every window of 7 pixels or more.

        On scenes that are only partly homogeneous the estimate drifts slightly
        low, reaching about 4.2 against a true 4.4 when half the scene is
        textured. Low is the safe direction, since a lower ENL yields a larger
        standard error and a larger texture estimate rather than overstating
        either.

    ``mode``
        Peak of a smoothed histogram of per-block ENL. This is the textbook
        presentation and is accurate when homogeneous regions dominate, but it
        fails when they do not, because the histogram peak then belongs to the
        textured population. On the same simulations it returned 4.45 against a
        true 4.4 for a fully homogeneous scene and 1.72 once half the scene was
        textured. Offered for comparison and for imagery known to be uniform.

    ``percentile``
        Plain upper percentile of per-block ENL with no correction. Biased high
        by construction. Included so the correction in the default method can be
        inspected rather than taken on trust.

    Parameters
    ----------
    intensity
        Two-dimensional linear-power array. NaN is handled by skipping any block
        that is not fully valid.
    window
        Block side length in pixels. Small enough that many blocks fall inside
        homogeneous regions, large enough for a usable per-block estimate. At
        ``window = 9`` each block holds 81 pixels and has a per-block relative
        standard error near 16 percent. Values below 7 are not recommended,
        because the correction becomes sensitive to the normal approximation it
        relies on.
    min_windows
        Minimum number of fully valid blocks required to report a result.
    homogeneity_percentile
        Keeps the blocks in this lowest percentile of within-block variability.
        Smaller is stricter. The ``percentile`` method uses its complement.
    bins
        Histogram resolution used when ``method='mode'``.

    Returns
    -------
    dict
        ``enl`` with the estimate, ``n_windows`` with the count of valid blocks,
        ``homogeneous_fraction`` with the share of blocks consistent with that
        ENL, and the block distribution's median and interquartile range.

        ``homogeneous_fraction`` is the diagnostic worth checking. It tracked the
        true homogeneous share closely in simulation, returning 0.94, 0.71, 0.46,
        0.22 and 0.10 for scenes that were 100, 75, 50, 25 and 10 percent
        homogeneous. A low value means few blocks supported the estimate, so it
        should be read as a lower bound on the true ENL.
    """
    arr = np.asarray(intensity, dtype=np.float64)
    if already_db:
        arr = to_linear(arr)
    if arr.ndim != 2:
        raise ValueError(f"estimate_scene_enl needs a 2-D array, got {arr.ndim}-D")
    if window < 3:
        raise ValueError("window must be at least 3 pixels")

    rows = (arr.shape[0] // window) * window
    cols = (arr.shape[1] // window) * window
    if rows == 0 or cols == 0:
        return {"enl": float("nan"), "n_windows": 0}

    # Non-overlapping blocks, so each estimate is independent of the others.
    blocks = (
        arr[:rows, :cols]
        .reshape(rows // window, window, cols // window, window)
        .swapaxes(1, 2)
        .reshape(-1, window * window)
    )

    valid = np.all(np.isfinite(blocks) & (blocks > 0), axis=1)
    blocks = blocks[valid]
    if blocks.shape[0] < min_windows:
        log.debug(
            "Only %d fully valid %dx%d blocks, need %d",
            blocks.shape[0],
            window,
            window,
            min_windows,
        )
        return {"enl": float("nan"), "n_windows": int(blocks.shape[0])}

    means = blocks.mean(axis=1)
    variances = blocks.var(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        cv_squared = np.where(means > 0, variances / (means * means), np.nan)
    cv_squared = cv_squared[np.isfinite(cv_squared) & (cv_squared > 0)]
    if cv_squared.size < min_windows:
        return {"enl": float("nan"), "n_windows": int(cv_squared.size)}

    # Per-block ENL, needed by the mode and percentile paths and by the
    # diagnostic below.
    local = 1.0 / cv_squared
    n_block = window * window
    noise = np.sqrt(2.0 / n_block)

    if method == "homogeneous":
        low = float(np.percentile(cv_squared, homogeneity_percentile))
        # The low percentile of a homogeneous population sits below its true
        # value by the estimator's own noise, so scale back up before
        # inverting. Guarded because a very small window would drive the
        # denominator to zero.
        shift = 1.0 - _normal_quantile(1.0 - homogeneity_percentile / 100.0) * noise
        estimate = 1.0 / (low / max(shift, 0.1))
    elif method == "percentile":
        estimate = float(np.percentile(local, 100.0 - homogeneity_percentile))
    elif method == "mode":
        estimate = _histogram_mode(local, bins=bins)
    else:
        raise ValueError("method must be 'homogeneous', 'percentile' or 'mode'")

    # Share of blocks consistent with the estimate, within two standard
    # deviations of the per-block sampling noise.
    consistent = np.abs(local - estimate) <= 2.0 * estimate * noise
    q25, q50, q75 = np.percentile(local, [25, 50, 75])

    return {
        "enl": float(estimate),
        "n_windows": int(local.size),
        "homogeneous_fraction": float(consistent.mean()),
        "window_median": float(q50),
        "window_iqr": float(q75 - q25),
        "method": method,
    }


# Standard normal quantiles for the percentiles the de-biasing needs. A short
# table with linear interpolation keeps the package free of a SciPy dependency,
# which matters because SciPy is otherwise not required anywhere in sarpanel.
_NORMAL_QUANTILES = {
    0.50: 0.0000,
    0.75: 0.6745,
    0.80: 0.8416,
    0.85: 1.0364,
    0.90: 1.2816,
    0.95: 1.6449,
    0.975: 1.9600,
    0.99: 2.3263,
    0.995: 2.5758,
}


def _normal_quantile(p: float) -> float:
    """Standard normal quantile, interpolated from a short table."""
    keys = sorted(_NORMAL_QUANTILES)
    p = min(max(p, keys[0]), keys[-1])
    return float(np.interp(p, keys, [_NORMAL_QUANTILES[k] for k in keys]))


def _histogram_mode(values: np.ndarray, bins: int = 120) -> float:
    """
    Peak of a lightly smoothed histogram.

    Smoothing matters because an unsmoothed histogram's peak bin is sensitive to
    bin placement, which makes the estimate jitter between runs on the same
    data. A three-bin moving average stabilises the location without shifting
    it. Implemented with numpy alone to avoid a new dependency.
    """
    # Clip the long upper tail before binning, otherwise almost every value
    # lands in the first few bins and the peak cannot be located.
    upper = np.percentile(values, 99.0)
    clipped = values[values <= upper]
    if clipped.size < 10:
        clipped = values

    counts, edges = np.histogram(clipped, bins=bins)
    if counts.sum() == 0:
        return float(np.median(values))

    kernel = np.ones(3) / 3.0
    smoothed = np.convolve(counts.astype(np.float64), kernel, mode="same")
    peak = int(np.argmax(smoothed))
    return float(0.5 * (edges[peak] + edges[peak + 1]))


def coefficient_of_variation(
    intensity: np.ndarray, already_db: bool = False
) -> float:
    """
    Linear-domain coefficient of variation.

    For fully developed speckle with L looks the CV is ``1/sqrt(L)``, so values
    materially above that indicate real spatial structure rather than speckle.
    This is the raw observable; :func:`texture_cv` converts it into a texture
    estimate once the scene ENL is known.
    """
    arr = np.asarray(intensity, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if already_db:
        arr = to_linear(arr)
    arr = arr[arr > 0]
    if arr.size < 2:
        return float("nan")
    mean = arr.mean()
    if mean <= 0:
        return float("nan")
    return float(arr.std(ddof=1) / mean)


def texture_cv(observed_cv: float, enl: float) -> float:
    """
    Remove the speckle contribution from an observed coefficient of variation.

    Under the standard multiplicative speckle model,

    ``cv_obs**2 = 1/L + cv_tex**2 * (1 + 1/L)``

    so the texture term inverts to

    ``cv_tex**2 = (cv_obs**2 - 1/L) / (1 + 1/L)``.

    This is the quantity that answers whether a polygon is genuinely
    heterogeneous, because it is comparable across polygons of different sizes
    and across scenes processed with different look counts. The raw CV is not,
    since a small polygon and a large one with identical ground conditions have
    the same expected CV but very different reliability, and a scene with fewer
    looks inflates every polygon's CV at once.

    Returns 0.0 when the observed CV falls at or below the speckle floor, which
    means the data are consistent with a uniform target, and NaN when the ENL is
    unusable.
    """
    if not np.isfinite(observed_cv) or not np.isfinite(enl) or enl <= 0:
        return float("nan")
    speckle_var = 1.0 / enl
    excess = observed_cv**2 - speckle_var
    if excess <= 0:
        return 0.0
    return float(np.sqrt(excess / (1.0 + speckle_var)))


def autocorrelation_inflation_factor(
    intensity: np.ndarray,
    already_db: bool = False,
    max_lag: int = 8,
    min_correlation: float = 0.05,
) -> float:
    """
    Variance inflation factor of the spatial autocorrelation.

    The variance of a mean over ``n`` correlated samples is
    ``(sigma**2 / n) * kappa`` rather than ``sigma**2 / n``, where

    ``kappa = sum over 2-D lags of rho(lag)``

    and ``rho`` is the normalized autocovariance. For independent pixels only
    the zero lag contributes and ``kappa = 1``. Interpolation during terrain
    correction, range oversampling, and any speckle filter all push it above
    one.

    Estimating this from the data matters because the magnitude depends on the
    processing chain rather than on anything knowable in advance. A nearest
    neighbour resampling leaves it near one while a bilinear or bicubic
    resampling onto a finer grid can push it to four or beyond.

    Parameters
    ----------
    intensity
        Two-dimensional linear-power array. The mean is divided out before the
        autocorrelation is computed, so a smooth large-scale trend across the
        patch will inflate the result; pass a reasonably uniform patch.
    max_lag
        Largest lag included in each direction. Truncation is necessary because
        the sample autocorrelation at large lags is mostly noise, and summing
        that noise would inflate the factor without bound.
    min_correlation
        Lags whose correlation falls below this are treated as zero. Combined
        with ``max_lag`` this keeps the sum to the genuine correlation support.

    Returns
    -------
    float
        ``kappa``, at least 1.0. NaN if the patch is too small or degenerate.
    """
    arr = np.asarray(intensity, dtype=np.float64)
    if already_db:
        arr = to_linear(arr)
    if arr.ndim != 2:
        raise ValueError("autocorrelation_inflation_factor needs a 2-D array")

    finite = np.isfinite(arr) & (arr > 0)
    if finite.sum() < 4 * max_lag * max_lag or finite.mean() < 0.5:
        return float("nan")

    mean = arr[finite].mean()
    if mean <= 0:
        return float("nan")

    # Work with fluctuations about the mean, with gaps set to zero so they
    # contribute nothing to the covariance rather than biasing it.
    field = np.where(finite, arr / mean - 1.0, 0.0)
    total_var = float(np.var(field[finite]))
    if total_var <= 0:
        return float("nan")

    # FFT-based 2-D autocovariance, zero padded to avoid circular wraparound.
    shape = (field.shape[0] * 2, field.shape[1] * 2)
    spectrum = np.fft.rfft2(field, s=shape)
    acov = np.fft.irfft2(spectrum * np.conj(spectrum), s=shape)

    # Normalize by the number of contributing pairs at each lag, then by the
    # zero-lag value so rho(0, 0) == 1 exactly.
    counts = np.fft.irfft2(
        np.abs(np.fft.rfft2(finite.astype(np.float64), s=shape)) ** 2, s=shape
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        rho = np.where(counts > 0, acov / np.maximum(counts, 1.0), 0.0)
    if rho[0, 0] <= 0:
        return float("nan")
    rho = rho / rho[0, 0]

    lag = min(max_lag, min(field.shape) - 1)
    quadrant = rho[: lag + 1, : lag + 1]
    kept = np.where(quadrant >= min_correlation, quadrant, 0.0)

    # The quadrant covers non-negative lags only. By symmetry the full 2-D sum
    # counts each non-zero row lag and each non-zero column lag twice.
    weights = np.ones_like(kept)
    weights[1:, :] *= 2.0
    weights[:, 1:] *= 2.0
    kappa = float((kept * weights).sum())
    return max(1.0, kappa)


def effective_sample_size(n_pixels: int, inflation_factor: float = 1.0) -> float:
    """
    Independent sample count implied by a pixel count and its correlation.

    Correlated pixels carry less information than their count suggests, so the
    pixel count is divided by the variance inflation factor from
    :func:`autocorrelation_inflation_factor`. With ``inflation_factor = 1`` this
    returns the pixel count unchanged, which is correct only for genuinely
    independent pixels.
    """
    if n_pixels < 1:
        return float("nan")
    if not np.isfinite(inflation_factor) or inflation_factor < 1.0:
        inflation_factor = 1.0
    return float(n_pixels) / float(inflation_factor)


def speckle_standard_error_db(n_effective: float, enl: float) -> float:
    """
    Standard error of a polygon-mean backscatter, in decibels.

    The mean of ``n_effective`` independent samples, each with ``enl`` looks,
    has relative standard error ``1 / sqrt(n_effective * enl)`` in linear power.
    Propagating through the log transform multiplies by ``10 / ln(10)``.

    The first argument must be an independent sample count, not a raw pixel
    count, whenever neighboring pixels are correlated. Passing the pixel count
    directly understates the error by the square root of the inflation factor,
    which after bilinear resampling onto a finer grid is a factor of about two.
    Use :func:`effective_sample_size` to convert.

    This is the weight a downstream model should use. It varies by an order of
    magnitude between a small polygon and a large one, so treating every
    observation as equally informative discards real and measurable differences
    in precision.
    """
    if not np.isfinite(enl) or enl <= 0:
        return float("nan")
    if not np.isfinite(n_effective) or n_effective < 1:
        return float("nan")
    relative = 1.0 / np.sqrt(n_effective * enl)
    return float((10.0 / np.log(10.0)) * relative)


def aggregate_polygon(
    intensity: np.ndarray,
    enl: float,
    inflation_factor: float = 1.0,
    already_db: bool = False,
    trim_percentile: float | None = None,
) -> dict[str, float]:
    """
    Reduce one polygon's pixels on one date to an estimate with a precision.

    This is where the measured scene ENL is put to use. The pixel values give
    the mean, the scene ENL gives the speckle level those pixels were formed
    with, and the correlation inflation gives how many of them are statistically
    independent. Together they turn a polygon mean into an estimate with a
    standard error, and separate genuine spatial structure from speckle.

    Pass the ``scene_enl`` and ``correlation_inflation`` columns that the
    pipeline records, not a per-polygon ENL estimate. A moment estimate taken
    over this polygon's own pixels would absorb its texture and so understate
    the look count, which would in turn overstate the standard error and erase
    the texture signal it was meant to measure.

    Parameters
    ----------
    intensity
        Linear-power pixel values for one polygon on one date.
    enl
        Scene equivalent number of looks.
    inflation_factor
        Variance inflation from inter-pixel correlation. Leaving it at 1 assumes
        independent pixels, which understates the standard error after any
        resampling step.
    already_db
        Set when the input is in decibels.
    trim_percentile
        Symmetric trim applied before computing the variability statistics, to
        limit the influence of isolated bright scatterers. The mean is always
        computed on the untrimmed sample, because trimming a skewed
        distribution biases its mean.

    Returns
    -------
    dict
        ``mean_linear``, ``mean_db``, ``n_pixels``, ``n_effective``, ``cv``,
        ``texture_cv`` and ``se_db``.
    """
    arr = np.asarray(intensity, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if already_db:
        arr = to_linear(arr)
    arr = arr[arr > 0]

    empty = {
        "mean_linear": float("nan"),
        "mean_db": float("nan"),
        "n_pixels": 0,
        "n_effective": float("nan"),
        "cv": float("nan"),
        "texture_cv": float("nan"),
        "se_db": float("nan"),
    }
    if arr.size == 0:
        return empty

    mean_linear = float(arr.mean())
    n_pixels = int(arr.size)
    n_effective = effective_sample_size(n_pixels, inflation_factor)

    sample = arr
    if trim_percentile and arr.size >= 16:
        lo, hi = np.percentile(arr, [trim_percentile, 100.0 - trim_percentile])
        trimmed = arr[(arr >= lo) & (arr <= hi)]
        if trimmed.size >= 16:
            sample = trimmed

    cv = coefficient_of_variation(sample)
    return {
        "mean_linear": mean_linear,
        "mean_db": float(to_db(mean_linear)),
        "n_pixels": n_pixels,
        "n_effective": float(n_effective),
        "cv": float(cv),
        "texture_cv": texture_cv(cv, enl),
        "se_db": speckle_standard_error_db(n_effective, enl),
    }


def gamma_change_statistic(
    intensity_a: np.ndarray,
    intensity_b: np.ndarray,
    looks_a: float,
    looks_b: float,
    already_db: bool = False,
    inflation_a: float = 1.0,
    inflation_b: float = 1.0,
) -> dict[str, float]:
    """
    Likelihood-ratio test for a change in mean backscatter between two dates.

    Treating each sample mean as Gamma distributed with a known total look
    count, the ratio of the pooled likelihood to the separate likelihoods forms
    a statistic that is asymptotically chi-squared with one degree of freedom
    under the null of no change.

    This replaces the fixed decibel thresholds that most amplitude change
    detection uses. Because the null distribution depends on the total look
    count, a threshold calibrated on large polygons is wrong on small ones,
    which is the failure mode that tracking looks removes.

    The total look count per date is ``looks * n_effective``, so the inflation
    factors matter here for the same reason they matter for the standard error.
    Leaving them at one on correlated pixels inflates the statistic and so
    overstates significance.
    """
    a = np.asarray(intensity_a, dtype=np.float64)
    b = np.asarray(intensity_b, dtype=np.float64)
    if already_db:
        a, b = to_linear(a), to_linear(b)
    a = a[np.isfinite(a) & (a > 0)]
    b = b[np.isfinite(b) & (b > 0)]
    if a.size == 0 or b.size == 0:
        return {
            "log_ratio_db": float("nan"),
            "statistic": float("nan"),
            "n_a": 0,
            "n_b": 0,
        }

    mean_a, mean_b = a.mean(), b.mean()
    la = looks_a * effective_sample_size(a.size, inflation_a)
    lb = looks_b * effective_sample_size(b.size, inflation_b)

    pooled = (la * mean_a + lb * mean_b) / (la + lb)
    stat = 2.0 * (la * np.log(pooled / mean_a) + lb * np.log(pooled / mean_b))
    return {
        "log_ratio_db": float(10.0 * np.log10(mean_b / mean_a)),
        "statistic": float(stat),
        "effective_looks_a": float(la),
        "effective_looks_b": float(lb),
        "n_a": int(a.size),
        "n_b": int(b.size),
    }
