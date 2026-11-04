"""
Radiometry tests.

The ENL estimator is validated against simulated speckle with a known look
count, which is the only way to be confident it is measuring what it claims.
"""

import numpy as np
import pytest

from sarpanel import radiometry as rad


@pytest.mark.parametrize("true_looks", [1.0, 4.4, 12.0, 40.0])
def test_enl_recovers_known_look_count(true_looks):
    """
    L-look intensity is Gamma(shape=L, scale=sigma/L). The estimator must
    return L to within sampling error, which at n=200000 is well under 1%.
    """
    rng = np.random.default_rng(42)
    sigma = 0.3
    sample = rng.gamma(shape=true_looks, scale=sigma / true_looks, size=200_000)
    assert rad.estimate_enl(sample) == pytest.approx(true_looks, rel=0.02)


@pytest.mark.parametrize("true_looks", [1.0, 4.4, 12.0])
def test_cv_matches_inverse_sqrt_looks(true_looks):
    rng = np.random.default_rng(7)
    sample = rng.gamma(shape=true_looks, scale=1.0 / true_looks, size=200_000)
    assert rad.coefficient_of_variation(sample) == pytest.approx(
        1.0 / np.sqrt(true_looks), rel=0.02
    )


def test_enl_computed_on_db_values_is_wrong():
    """
    Guards the documented failure mode. Estimating ENL on decibel values gives
    a number with no relationship to the look count, because the log transform
    stabilises variance. The estimator must be given linear power.
    """
    rng = np.random.default_rng(1)
    sample = rng.gamma(shape=4.4, scale=1.0 / 4.4, size=100_000)
    correct = rad.estimate_enl(sample)
    wrong = rad.estimate_enl(rad.to_db(sample), already_db=False)
    assert correct == pytest.approx(4.4, rel=0.05)
    assert not (3.0 < wrong < 6.0)


def test_already_db_flag_round_trips():
    rng = np.random.default_rng(3)
    sample = rng.gamma(shape=8.0, scale=1.0 / 8.0, size=50_000)
    from_linear = rad.estimate_enl(sample)
    from_db = rad.estimate_enl(rad.to_db(sample), already_db=True)
    assert from_db == pytest.approx(from_linear, rel=1e-6)


def test_enl_returns_nan_below_minimum_sample():
    assert np.isnan(rad.estimate_enl(np.array([0.1, 0.2, 0.3])))


def test_db_averaging_is_biased_low():
    """
    The documented reason aggregation happens in linear power. The bias grows
    with heterogeneity, so it is correlated with within-field variability
    rather than being independent noise.
    """
    rng = np.random.default_rng(11)
    homogeneous = rng.gamma(shape=40.0, scale=1.0 / 40.0, size=100_000)
    heterogeneous = rng.gamma(shape=1.0, scale=1.0, size=100_000)

    for sample in (homogeneous, heterogeneous):
        correct = rad.mean_linear_then_db(sample)
        naive = float(np.nanmean(rad.to_db(sample)))
        assert naive < correct

    bias_low = rad.mean_linear_then_db(homogeneous) - np.nanmean(rad.to_db(homogeneous))
    bias_high = rad.mean_linear_then_db(heterogeneous) - np.nanmean(
        rad.to_db(heterogeneous)
    )
    assert bias_high > bias_low > 0


def test_to_db_maps_nonpositive_to_nan():
    out = rad.to_db(np.array([1.0, 0.0, -1.0, 10.0]))
    assert out[0] == pytest.approx(0.0)
    assert np.isnan(out[1]) and np.isnan(out[2])
    assert out[3] == pytest.approx(10.0)


def test_standard_error_shrinks_with_pixels_and_looks():
    big = rad.speckle_standard_error_db(1000, 4.4)
    small = rad.speckle_standard_error_db(20, 4.4)
    assert small > big
    # Quadrupling the sample halves the error.
    assert rad.speckle_standard_error_db(400, 4.4) == pytest.approx(
        rad.speckle_standard_error_db(100, 4.4) / 2, rel=1e-6
    )


def test_nominal_looks_slc_versus_grd():
    slc = rad.nominal_looks(10.0, "SLC")
    grd = rad.nominal_looks(10.0, "GRD")
    # GRD arrives already multilooked, so at native spacing it carries the
    # product's nominal ENL while SLC starts from a single look.
    assert grd["looks_nominal"] == pytest.approx(rad.S1_IW_GRDH_NOMINAL_ENL)
    assert slc["looks_range"] > 1.0
    assert rad.nominal_looks(40.0, "SLC")["looks_nominal"] > slc["looks_nominal"]


def test_change_statistic_is_near_zero_under_the_null():
    rng = np.random.default_rng(5)
    a = rng.gamma(4.4, 1.0 / 4.4, 2000)
    b = rng.gamma(4.4, 1.0 / 4.4, 2000)
    result = rad.gamma_change_statistic(a, b, 4.4, 4.4)
    assert abs(result["log_ratio_db"]) < 0.3
    assert result["statistic"] < 10.0


def test_change_statistic_detects_a_real_change():
    rng = np.random.default_rng(6)
    a = rng.gamma(4.4, 1.0 / 4.4, 2000)
    b = rng.gamma(4.4, 2.0 / 4.4, 2000)  # 3 dB brighter
    result = rad.gamma_change_statistic(a, b, 4.4, 4.4)
    assert result["log_ratio_db"] == pytest.approx(3.0, abs=0.3)
    assert result["statistic"] > 100.0


def test_change_statistic_scales_with_sample_size():
    """
    The same decibel change is more significant with more effective looks.
    This is why a fixed decibel threshold calibrated on large fields is wrong
    on small ones, and why look counts are tracked per observation.
    """
    rng = np.random.default_rng(8)
    small_a = rng.gamma(4.4, 1.0 / 4.4, 30)
    small_b = rng.gamma(4.4, 1.3 / 4.4, 30)
    large_a = rng.gamma(4.4, 1.0 / 4.4, 3000)
    large_b = rng.gamma(4.4, 1.3 / 4.4, 3000)

    small = rad.gamma_change_statistic(small_a, small_b, 4.4, 4.4)
    large = rad.gamma_change_statistic(large_a, large_b, 4.4, 4.4)
    assert large["statistic"] > small["statistic"]


# ---------------------------------------------------------------------------
# scene-level ENL, the conventional estimator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("true_looks", [4.4, 12.0, 40.0])
def test_scene_enl_recovers_known_looks_on_homogeneous_imagery(true_looks):
    rng = np.random.default_rng(7)
    img = rng.gamma(true_looks, 1.0 / true_looks, size=(400, 400))
    result = rad.estimate_scene_enl(img)
    assert result["enl"] == pytest.approx(true_looks, rel=0.08)
    assert result["homogeneous_fraction"] > 0.6


def test_scene_enl_survives_a_half_textured_scene():
    """
    The textbook histogram mode locks onto whichever population is denser, so
    it reports the textured value once homogeneous area stops dominating. The
    default estimator selects on low variability instead and keeps working.
    """
    rng = np.random.default_rng(1)
    L = 4.4
    n = 360
    img = rng.gamma(L, 1.0 / L, size=(n, n))
    texture = np.ones((n, n))
    texture[:, n // 2 :] = rng.gamma(3.0, 1 / 3.0, size=(n, n // 2))
    mixed = img * texture

    default = rad.estimate_scene_enl(mixed)["enl"]
    mode = rad.estimate_scene_enl(mixed, method="mode")["enl"]

    assert default == pytest.approx(L, rel=0.15)
    assert mode < 0.6 * L, "mode is expected to fail here; that is why it is not default"


def test_scene_enl_errs_low_rather_than_high_as_texture_grows():
    """
    A low ENL yields a larger standard error and a larger texture estimate, so
    drifting low is the safe direction for a tool whose output feeds a model.
    """
    rng = np.random.default_rng(3)
    L = 4.4
    n = 360
    estimates = []
    for homogeneous_fraction in (1.0, 0.5, 0.25):
        img = rng.gamma(L, 1.0 / L, size=(n, n))
        texture = np.ones((n, n))
        cols = int(n * (1 - homogeneous_fraction))
        if cols:
            texture[:, n - cols :] = rng.gamma(3.0, 1 / 3.0, size=(n, cols))
        estimates.append(rad.estimate_scene_enl(img * texture)["enl"])

    assert all(e <= L * 1.1 for e in estimates)
    assert estimates == sorted(estimates, reverse=True)


def test_scene_enl_reports_nan_rather_than_guessing_when_data_is_thin():
    tiny = np.full((10, 10), 1.0)
    result = rad.estimate_scene_enl(tiny)
    assert np.isnan(result["enl"])


def test_enl_on_decibels_is_rejected_by_the_scene_estimator():
    """Converting back is the caller's job and the flag exists to do it."""
    rng = np.random.default_rng(5)
    img = rng.gamma(4.4, 1 / 4.4, size=(200, 200))
    from_linear = rad.estimate_scene_enl(img)["enl"]
    from_db = rad.estimate_scene_enl(rad.to_db(img), already_db=True)["enl"]
    assert from_db == pytest.approx(from_linear, rel=1e-6)


# ---------------------------------------------------------------------------
# spatial correlation and effective sample size
# ---------------------------------------------------------------------------


def test_inflation_factor_is_one_for_independent_pixels():
    rng = np.random.default_rng(11)
    img = rng.gamma(4.4, 1 / 4.4, size=(300, 300))
    assert rad.autocorrelation_inflation_factor(img) == pytest.approx(1.0, abs=0.1)


@pytest.mark.parametrize("box,expected", [(2, 4.0), (3, 9.0)])
def test_inflation_factor_recovers_known_smoothing(box, expected):
    """
    Averaging over a box of side k makes k**2 pixels act as one, so the
    variance inflation factor of a mean over them should be about k**2.
    """
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(13)
    img = rng.gamma(4.4, 1 / 4.4, size=(400, 400))
    smoothed = scipy_ndimage.uniform_filter(img, size=box, mode="reflect")
    assert rad.autocorrelation_inflation_factor(smoothed) == pytest.approx(
        expected, rel=0.1
    )


def test_smoothing_creates_no_information():
    """
    The core reason the standard error needs the effective sample size.
    Smoothing raises the per-pixel ENL, but it adds no information, so
    n_effective times ENL must stay put. Using the raw pixel count instead
    would credit the variance reduction while ignoring the correlation that
    caused it.
    """
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(17)
    true_looks = 4.4
    img = rng.gamma(true_looks, 1 / true_looks, size=(400, 400))
    smoothed = scipy_ndimage.uniform_filter(img, size=2, mode="reflect")

    baseline = img.size * true_looks

    raw = smoothed.size * rad.estimate_enl(smoothed)
    corrected = (
        rad.effective_sample_size(
            smoothed.size, rad.autocorrelation_inflation_factor(smoothed)
        )
        * rad.estimate_enl(smoothed)
    )

    assert raw > 3 * baseline, "the uncorrected product should be badly inflated"
    assert corrected == pytest.approx(baseline, rel=0.1)


def test_effective_sample_size_floors_the_inflation_factor_at_one():
    assert rad.effective_sample_size(100, 1.0) == 100
    assert rad.effective_sample_size(100, 0.5) == 100  # cannot gain samples
    assert rad.effective_sample_size(100, 4.0) == 25


def test_standard_error_doubles_when_four_pixels_act_as_one():
    independent = rad.speckle_standard_error_db(1000, 4.4)
    correlated = rad.speckle_standard_error_db(
        rad.effective_sample_size(1000, 4.0), 4.4
    )
    assert correlated == pytest.approx(2 * independent, rel=1e-6)


# ---------------------------------------------------------------------------
# texture separated from speckle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("true_texture", [0.2, 0.5, 0.8])
def test_texture_cv_recovers_known_texture(true_texture):
    rng = np.random.default_rng(19)
    looks = 4.4
    shape = 1.0 / true_texture**2
    texture = rng.gamma(shape, 1.0 / shape, size=200_000)
    observed = rng.gamma(looks, 1.0 / looks, size=200_000) * texture
    estimated = rad.texture_cv(rad.coefficient_of_variation(observed), looks)
    assert estimated == pytest.approx(true_texture, rel=0.08)


def test_texture_cv_is_zero_for_a_uniform_target():
    rng = np.random.default_rng(23)
    looks = 4.4
    pure_speckle = rng.gamma(looks, 1.0 / looks, size=200_000)
    assert rad.texture_cv(
        rad.coefficient_of_variation(pure_speckle), looks
    ) == pytest.approx(0.0, abs=0.05)


def test_texture_cv_is_comparable_across_look_counts():
    """
    The raw CV is not comparable between scenes, because fewer looks inflate
    every polygon's CV at once. The texture term removes that dependence, which
    is what makes it usable as a covariate across a multi-year archive.
    """
    rng = np.random.default_rng(29)
    true_texture = 0.4
    shape = 1.0 / true_texture**2
    recovered = []
    for looks in (2.0, 4.4, 20.0):
        texture = rng.gamma(shape, 1.0 / shape, size=200_000)
        observed = rng.gamma(looks, 1.0 / looks, size=200_000) * texture
        recovered.append(
            rad.texture_cv(rad.coefficient_of_variation(observed), looks)
        )
    assert max(recovered) - min(recovered) < 0.05


# ---------------------------------------------------------------------------
# nominal looks, resolution rather than sample spacing
# ---------------------------------------------------------------------------


def test_nominal_looks_uses_resolution_not_sample_spacing():
    """
    Regression test. Sentinel-1 range samples are spaced 2.3 m apart inside a
    2.7 m resolution cell, so counting samples credits a 10 m pixel with 4.35
    range looks when it can hold at most 3.70 independent ones. An earlier
    version counted samples and so overstated the nominal look count.
    """
    slc = rad.nominal_looks(10.0, "SLC")
    assert slc["looks_range"] == pytest.approx(10.0 / 2.7, rel=1e-6)
    assert slc["looks_range"] < 10.0 / 2.3


def test_nominal_looks_reports_sub_resolution_oversampling():
    """
    A 10 m pixel is well inside the 22 m azimuth resolution, so azimuth is
    being interpolated rather than multilooked. That contributes no looks and
    instead correlates neighbouring pixels, which the oversampling ratio
    records so it is not mistaken for information.
    """
    slc = rad.nominal_looks(10.0, "SLC")
    assert slc["looks_azimuth"] == 1.0
    assert slc["oversampling_azimuth"] == pytest.approx(2.2, rel=1e-6)


def test_nominal_looks_is_an_upper_bound_on_measured_enl():
    """
    Nominal is geometry and ENL is measurement, and correlation between looks
    means measurement cannot exceed geometry for genuinely single-look input.
    """
    rng = np.random.default_rng(31)
    nominal = rad.nominal_looks(40.0, "SLC")["looks_nominal"]
    # Simulate imagery whose true look count sits at the nominal value.
    img = rng.gamma(nominal, 1.0 / nominal, size=(300, 300))
    assert rad.estimate_scene_enl(img)["enl"] <= nominal * 1.1


# ---------------------------------------------------------------------------
# polygon aggregation
# ---------------------------------------------------------------------------


def test_aggregate_polygon_separates_texture_at_matched_mean_and_count():
    """
    Two polygons with the same mean backscatter and the same pixel count, one
    uniform and one textured. The means and standard errors should match, since
    those depend only on the sample size and the scene look count, while the
    texture statistic should separate them.
    """
    rng = np.random.default_rng(37)
    looks = 4.4
    n = 4000
    uniform = rng.gamma(looks, 0.1 / looks, size=n)
    textured = rng.gamma(looks, 1.0 / looks, size=n) * rng.gamma(4, 0.1 / 4, size=n)

    a = rad.aggregate_polygon(uniform, enl=looks, inflation_factor=4.0)
    b = rad.aggregate_polygon(textured, enl=looks, inflation_factor=4.0)

    assert a["mean_db"] == pytest.approx(b["mean_db"], abs=0.5)
    assert a["se_db"] == pytest.approx(b["se_db"], rel=1e-9)
    assert a["texture_cv"] < 0.15
    assert b["texture_cv"] > 0.35


def test_aggregate_polygon_applies_the_inflation_factor():
    rng = np.random.default_rng(41)
    sample = rng.gamma(4.4, 1 / 4.4, size=1000)
    plain = rad.aggregate_polygon(sample, enl=4.4)
    inflated = rad.aggregate_polygon(sample, enl=4.4, inflation_factor=4.0)
    assert inflated["n_effective"] == pytest.approx(plain["n_effective"] / 4)
    assert inflated["se_db"] == pytest.approx(2 * plain["se_db"], rel=1e-9)


def test_aggregate_polygon_means_in_linear_power():
    """The mean must not be computed in decibels, which biases it low."""
    rng = np.random.default_rng(43)
    sample = rng.gamma(1.0, 1.0, size=50_000)
    result = rad.aggregate_polygon(sample, enl=1.0)
    assert result["mean_db"] == pytest.approx(rad.to_db(sample.mean()), abs=1e-9)
    assert result["mean_db"] > float(np.nanmean(rad.to_db(sample)))


def test_aggregate_polygon_handles_an_empty_sample():
    result = rad.aggregate_polygon(np.array([]), enl=4.4)
    assert result["n_pixels"] == 0
    assert np.isnan(result["mean_db"])
