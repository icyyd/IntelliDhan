from intellidhan_ingestor.providers.yahoo import _normalize_adjusted_ohlc


def test_adjusted_ohlc_repairs_only_floating_point_boundary_noise():
    result = _normalize_adjusted_ohlc(31.40, 31.57603645, 31.25, 31.576036450000004)
    assert result is not None
    assert result[1] == result[3]


def test_adjusted_ohlc_rejects_materially_corrupt_range():
    assert _normalize_adjusted_ohlc(31.40, 31.50, 31.25, 31.70) is None
