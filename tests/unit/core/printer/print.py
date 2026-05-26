"""Tests for print_analysis — formatted analysis output."""

from io import StringIO

from core.calculator import AnalysisResult, ZoneStats
from core.printer import print_analysis


def test_header_shows_level_label_when_zones_exist():
    # Arrange
    result = AnalysisResult(level="country", level_label="Country", zones={})
    out = StringIO()

    # Act
    print_analysis(result, level="country", groups={}, stream=out)
    output = out.getvalue()

    # Assert
    assert "ANALYSIS — COUNTRY" in output
    assert "═" in output


def test_warning_when_no_zones_available():
    # Arrange
    result = AnalysisResult(level="country", level_label="Country", zones={})
    out = StringIO()

    # Act
    print_analysis(result, level="country", groups={}, stream=out)
    output = out.getvalue()

    # Assert
    assert "No zones with" in output


def test_zone_stats_rendered_for_each_zone():
    # Arrange
    zones = {
        "France": ZoneStats(
            total=150,
            window=100,
            score_high=3500,
            level_label="Exceptional",
            level_arrow="↑",
            p90_label="High",
            p90_arrow="→",
            p90_now_km=800.0,
            cons_label="Decent",
            cons_arrow="↓",
            std_now_km=300.0,
            ci_lo=None,
            ci_hi=None,
        )
    }
    result = AnalysisResult(level="country", level_label="Country", zones=zones)
    groups = {"France": [{}] * 150}
    out = StringIO()

    # Act
    print_analysis(result, level="country", groups=groups, stream=out)
    output = out.getvalue()

    # Assert
    assert "▸ France" in output
    assert "Current level" in output
    assert "Exceptional" in output
    assert "Worst rounds" in output
    assert "Consistency" in output


def test_zones_sorted_by_score_ascending():
    # Arrange
    zones = {
        "Germany": ZoneStats(
            total=80,
            window=80,
            score_high=4000,
            level_label="High",
            level_arrow="→",
            p90_label="Decent",
            p90_arrow="→",
            p90_now_km=600.0,
            cons_label="High",
            cons_arrow="↑",
            std_now_km=200.0,
            ci_lo=None,
            ci_hi=None,
        ),
        "France": ZoneStats(
            total=150,
            window=100,
            score_high=3500,
            level_label="Exceptional",
            level_arrow="↑",
            p90_label="High",
            p90_arrow="→",
            p90_now_km=800.0,
            cons_label="Decent",
            cons_arrow="↓",
            std_now_km=300.0,
            ci_lo=None,
            ci_hi=None,
        ),
    }
    result = AnalysisResult(level="country", level_label="Country", zones=zones)
    out = StringIO()

    # Act
    print_analysis(result, level="country", groups={}, stream=out)
    output = out.getvalue()

    # Assert
    france_idx = output.index("▸ France")
    germany_idx = output.index("▸ Germany")
    assert france_idx < germany_idx


def test_confidence_interval_when_bootstrap_available():
    # Arrange
    zones = {
        "France": ZoneStats(
            total=150,
            window=100,
            score_high=3500,
            level_label="Exceptional",
            level_arrow="↑",
            p90_label="High",
            p90_arrow="→",
            p90_now_km=800.0,
            cons_label="Decent",
            cons_arrow="↓",
            std_now_km=300.0,
            ci_lo=500.0,
            ci_hi=1200.0,
        )
    }
    result = AnalysisResult(level="country", level_label="Country", zones=zones)
    groups = {"France": [{}] * 150}
    out = StringIO()

    # Act
    print_analysis(result, level="country", groups=groups, stream=out)
    output = out.getvalue()

    # Assert
    assert "reliable range" in output
    assert "500" in output
    assert "1,200" in output


def test_confusion_section_when_geo_level_has_conflicts():
    # Arrange
    zones = {
        "France": ZoneStats(
            total=150,
            window=100,
            score_high=3500,
            level_label="Exceptional",
            level_arrow="↑",
            p90_label="High",
            p90_arrow="→",
            p90_now_km=800.0,
            cons_label="Decent",
            cons_arrow="↓",
            std_now_km=300.0,
            ci_lo=None,
            ci_hi=None,
        )
    }
    result = AnalysisResult(level="country", level_label="Country", zones=zones)
    groups = {
        "France": [
            {
                "real_geo": {"country": "France"},
                "guess_geo": {"country": "Germany"},
                "distance_km": 500,
            },
            {
                "real_geo": {"country": "France"},
                "guess_geo": {"country": "Germany"},
                "distance_km": 600,
            },
            {
                "real_geo": {"country": "France"},
                "guess_geo": {"country": "France"},
                "distance_km": 50,
            },
        ]
        * 50
    }
    out = StringIO()

    # Act
    print_analysis(result, level="country", groups=groups, stream=out)
    output = out.getvalue()

    # Assert
    assert "Key confusion" in output
    assert "France" in output
    assert "Germany" in output
