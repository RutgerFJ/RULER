"""Unit tests for calc_strd module functions."""
import csv

import pytest

from core.utils.calculations.calc_strd import (
    read_mlva_data,
    calculate_strd_values,
    write_output,
)


@pytest.fixture
def sample_mlva_csv(tmp_path):
    """Create a sample MLVA CSV file for testing.
    
    Returns:
        Path to the created CSV file.
    """
    csv_file = tmp_path / "sample_mlva.csv"
    data = [
        ["Isolate", "CDR4", "CDR5", "CDR6"],
        ["ISO001", "10", "5", "8"],
        ["ISO002", "10", "7", "8"],
        ["ISO003", "12", "5", "DC"],
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(data)
    return csv_file


class TestReadMlvaData:
    """Tests for read_mlva_data function."""

    def test_read_mlva_data_basic(self, sample_mlva_csv):
        """Test reading basic MLVA data from CSV."""
        mlva_data, loci = read_mlva_data(str(sample_mlva_csv))
        
        assert loci == ["CDR4", "CDR5", "CDR6"]
        assert len(mlva_data) == 3
        assert mlva_data["ISO001"] == ["10", "5", "8"]
        assert mlva_data["ISO002"] == ["10", "7", "8"]
        assert mlva_data["ISO003"] == ["12", "5", "DC"]

    def test_read_mlva_data_preserves_dc_values(self, sample_mlva_csv):
        """Test that DC values are preserved."""
        mlva_data, _ = read_mlva_data(str(sample_mlva_csv))
        
        assert mlva_data["ISO003"][2] == "DC"

    def test_read_mlva_data_empty_file(self, tmp_path):
        """Test reading an empty CSV file."""
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("Isolate,CDR4,CDR5\n")
        
        mlva_data, loci = read_mlva_data(str(csv_file))
        
        assert loci == ["CDR4", "CDR5"]
        assert len(mlva_data) == 0


class TestCalculateStrdValues:
    """Tests for calculate_strd_values function."""

    def test_calculate_strd_identical_isolates(self):
        """Test STRD calculation for identical isolates."""
        mlva_data = {
            "ISO001": ["10", "5", "8"],
            "ISO002": ["10", "5", "8"],
        }
        loci = ["CDR4", "CDR5", "CDR6"]
        keep_loci = loci
        
        strds = calculate_strd_values(mlva_data, loci, keep_loci)
        
        assert len(strds) == 1
        assert strds[0] == ["ISO001", "ISO002", 0, 0]

    def test_calculate_strd_different_values(self):
        """Test STRD calculation for isolates with different values."""
        mlva_data = {
            "ISO001": ["10", "5", "8"],
            "ISO002": ["12", "7", "8"],
        }
        loci = ["CDR4", "CDR5", "CDR6"]
        keep_loci = loci
        
        strds = calculate_strd_values(mlva_data, loci, keep_loci)
        
        assert len(strds) == 1
        # CDR4: |10-12|=2, CDR5: |5-7|=2, CDR6: equal
        # STRD = 2+2 = 4, LV = 2
        assert strds[0] == ["ISO001", "ISO002", 4, 2]

    def test_calculate_strd_skip_loci(self):
        """Test STRD calculation with skipped loci."""
        mlva_data = {
            "ISO001": ["10", "5", "8"],
            "ISO002": ["12", "7", "9"],
        }
        loci = ["CDR4", "CDR5", "CDR6"]
        keep_loci = ["CDR4", "CDR5"]  # Skip CDR6
        
        strds = calculate_strd_values(mlva_data, loci, keep_loci)
        
        assert len(strds) == 1
        # CDR4: |10-12|=2, CDR5: |5-7|=2, CDR6 is skipped
        # STRD = 2+2 = 4, LV = 2
        assert strds[0] == ["ISO001", "ISO002", 4, 2]

    def test_calculate_strd_with_dc_values(self):
        """Test STRD calculation ignores DC  values."""
        mlva_data = {
            "ISO001": ["10", "5", "DC"],
            "ISO002": ["12", "7", "9"],
        }
        loci = ["CDR4", "CDR5", "CDR6"]
        keep_loci = loci
        
        strds = calculate_strd_values(mlva_data, loci, keep_loci)
        
        assert len(strds) == 1
        # CDR4: |10-12|=2, CDR5: |5-7|=2, CDR6: DC is skipped
        # STRD = 2+2 = 4, LV = 2
        assert strds[0] == ["ISO001", "ISO002", 4, 2]

    def test_calculate_strd_multiple_pairs(self):
        """Test STRD calculation for multiple isolate pairs."""
        mlva_data = {
            "ISO001": ["10", "5"],
            "ISO002": ["10", "5"],
            "ISO003": ["12", "7"],
        }
        loci = ["CDR4", "CDR5"]
        keep_loci = loci
        
        strds = calculate_strd_values(mlva_data, loci, keep_loci)
        
        assert len(strds) == 3
        # ISO001 vs ISO002: STRD=0, LV=0
        # ISO001 vs ISO003: STRD=4, LV=2
        # ISO002 vs ISO003: STRD=4, LV=2
        assert any(s[0] == "ISO001" and s[1] == "ISO002" and s[2] == 0 for s in strds)


class TestWriteOutput:
    """Tests for write_output function."""

    def test_write_output_to_file(self, tmp_path):
        """Test writing STRD output to a file."""
        output_file = tmp_path / "output.csv"
        strds = [
            ["ISO001", "ISO002", 4, 2],
            ["ISO001", "ISO003", 8, 3],
        ]
        
        write_output(strds, str(output_file))
        
        assert output_file.exists()
        with open(output_file, "r") as f:
            reader = csv.reader(f)
            lines = list(reader)
        
        assert lines[0] == ["Isolate1", "Isolate2", "STRD", "LV"]
        assert lines[1] == ["ISO001", "ISO002", "4", "2"]
        assert lines[2] == ["ISO001", "ISO003", "8", "3"]

    def test_write_output_empty_results(self, tmp_path):
        """Test writing empty STRD results."""
        output_file = tmp_path / "output.csv"
        strds = []
        
        write_output(strds, str(output_file))
        
        assert output_file.exists()
        with open(output_file, "r") as f:
            reader = csv.reader(f)
            lines = list(reader)
        
        assert lines[0] == ["Isolate1", "Isolate2", "STRD", "LV"]
        assert len(lines) == 1
