"""
Unit tests for N50 calculation utility.

Tests cover various scenarios including edge cases, normal cases,
and the examples from the docstring.
"""

import pytest

from core.utils.n50 import calculate_n50


def test_n50_basic_example_1():
    """Test N50 with lengths [100, 50, 40, 10]."""
    # Total = 200, 50% = 100
    # Sorted: [100, 50, 40, 10]
    # N50 = 100 (first contig at cumulative 100)
    result = calculate_n50([100, 50, 40, 10])
    assert result == 100


def test_n50_basic_example_2():
    """Test N50 with lengths [90, 80, 70, 60, 50]."""
    # Total = 350, 50% = 175
    # Sorted: [90, 80, 70, 60, 50]
    # Cumulative: 90, 170, 240...
    # N50 = 70 (reaches 240 >= 175)
    result = calculate_n50([90, 80, 70, 60, 50])
    assert result == 70


def test_n50_empty_list():
    """Test N50 with empty list returns 0."""
    result = calculate_n50([])
    assert result == 0


def test_n50_single_contig():
    """Test N50 with single contig."""
    # Total = 100, 50% = 50
    # N50 = 100 (single contig covers 50%)
    result = calculate_n50([100])
    assert result == 100


def test_n50_two_equal_contigs():
    """Test N50 with two equal-length contigs."""
    # Total = 200, 50% = 100
    # Sorted: [100, 100]
    # Cumulative: 100 >= 100, so N50 = 100
    result = calculate_n50([100, 100])
    assert result == 100


def test_n50_many_small_contigs():
    """Test N50 with many small contigs."""
    # Total = 100, 50% = 50
    # Sorted: [10, 10, 10, 10, 10, 10, 10, 10, 10, 10]
    # Cumulative: 10, 20, 30, 40, 50... N50 = 10 (at position 5, cumulative 50)
    result = calculate_n50([10] * 10)
    assert result == 10


def test_n50_one_large_many_small():
    """Test N50 with one large contig and many small ones."""
    # Total = 150, 50% = 75
    # Sorted: [100, 10, 10, 10, 10, 10]
    # Cumulative: 100 >= 75, so N50 = 100
    result = calculate_n50([100, 10, 10, 10, 10, 10])
    assert result == 100


def test_n50_realistic_assembly():
    """Test N50 with realistic genome assembly lengths."""
    # Realistic bacterial contig lengths
    contigs = [50000, 45000, 42000, 38000, 35000, 30000, 28000, 25000, 20000, 15000]
    # Total = 328000, 50% = 164000
    # Sorted (already sorted): same order
    # Cumulative: 50k, 95k, 137k, 175k... N50 at index 3 = 38000
    result = calculate_n50(contigs)
    assert result == 38000


def test_n50_unsorted_input():
    """Test that N50 correctly handles unsorted input."""
    # Should give same result regardless of input order
    lengths = [10, 100, 30, 50, 20]
    result = calculate_n50(lengths)
    
    # Expected: sorted [100, 50, 30, 20, 10]
    # Total = 210, 50% = 105
    # Cumulative: 100, 150... N50 = 50
    assert result == 50


def test_n50_with_very_large_numbers():
    """Test N50 with very large contig lengths."""
    # Lengths in millions of base pairs
    contigs = [1000000, 900000, 800000, 700000, 600000]
    # Total = 4000000, 50% = 2000000
    # Sorted: same order
    # Cumulative: 1M, 1.9M, 2.7M... N50 = 800000
    result = calculate_n50(contigs)
    assert result == 800000


def test_n50_precision_at_threshold():
    """Test N50 when cumulative length exactly equals 50% threshold."""
    # Total = 100, 50% = 50
    # Sorted: [50, 30, 20]
    # Cumulative: 50 == 50, so N50 = 50
    result = calculate_n50([50, 30, 20])
    assert result == 50


def test_n50_just_over_threshold():
    """Test N50 when cumulative length just exceeds 50% threshold."""
    # Total = 99, 50% = 49.5
    # Sorted: [50, 30, 19]
    # Cumulative: 50 >= 49.5, so N50 = 50
    result = calculate_n50([50, 30, 19])
    assert result == 50


def test_n50_identical_contigs():
    """Test N50 with all identical contig lengths."""
    # 5 contigs of 20bp each
    # Total = 100, 50% = 50
    # Cumulative: 20, 40, 60... N50 = 20
    result = calculate_n50([20, 20, 20, 20, 20])
    assert result == 20


def test_n50_order_independence():
    """Verify N50 is independent of input order."""
    lengths = [100, 50, 40, 10]
    reversed_lengths = [10, 40, 50, 100]
    
    result1 = calculate_n50(lengths)
    result2 = calculate_n50(reversed_lengths)
    
    assert result1 == result2
    assert result1 == 100


def test_n50_descending_sequence():
    """Test N50 with descending sequence of lengths."""
    # Total = 150, 50% = 75
    # Sorted: [100, 30, 20]
    # N50 = 100
    result = calculate_n50([100, 30, 20])
    assert result == 100


def test_n50_ascending_sequence():
    """Test N50 with ascending sequence of lengths."""
    # Total = 150, 50% = 75
    # Sorted: [100, 30, 20]
    # N50 = 100
    result = calculate_n50([20, 30, 100])
    assert result == 100
