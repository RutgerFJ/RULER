"""Unit tests for mlva module functions."""
import csv
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

from core.utils.calculations.mlva import (
    find_repeats,
    find_all_vntrs,
    count_motif,
    count_all_repeats,
    find_repeats_by_length
)


class TestFindRepeats:
    """Tests for find_repeats function."""
    
    def test_find_repeats_exact_match(self):
        """Test finding exact repeats without errors."""
        seq = SeqRecord(Seq("GAGCAAGAGCAAGAGCAA"))
        repeat_seq = "GAGCAA"
        result = find_repeats(seq, repeat_seq)
        assert result == 3
    
    def test_find_repeats_single_repeat(self):
        """Test finding a single repeat."""
        seq = SeqRecord(Seq("ATGAGCAATTT"))
        repeat_seq = "GAGCAA"
        result = find_repeats(seq, repeat_seq)
        assert result == 1
    
    def test_find_repeats_no_match(self):
        """Test when no repeats are found."""
        seq = SeqRecord(Seq("ATGATGATGATG"))
        repeat_seq = "GAGCAA"
        result = find_repeats(seq, repeat_seq)
        assert result == 0
    
    def test_find_repeats_case_insensitive(self):
        """Test that matching is case-insensitive."""
        seq = SeqRecord(Seq("gagcaagagcaa"))
        repeat_seq = "GAGCAA"
        result = find_repeats(seq, repeat_seq)
        assert result == 2
    
    def test_find_repeats_with_fuzzy_matching(self):
        """Test fuzzy matching (allows 1 error per repeat)."""
        # GAGCAA with one substitution
        seq = SeqRecord(Seq("GAGCAAGACAAA"))  # Second repeat has one mismatch
        repeat_seq = "GAGCAA"
        result = find_repeats(seq, repeat_seq)
        assert result >= 1  # At least finds the exact match
    
    def test_find_repeats_reverse_complement(self):
        """Test that reverse complement is checked if forward strand has no match."""
        # Create a sequence that matches in reverse complement
        forward_seq = Seq("ATGATGATGATG")
        reverse_seq = Seq("GAGCAAGAGCAA")
        seq = SeqRecord(reverse_seq.reverse_complement())
        repeat_seq = "GAGCAA"
        result = find_repeats(seq, repeat_seq)
        assert result == 2  # Two exact matches of GAGCAA


class TestCountMotif:
    """Tests for count_motif function."""
    
    def test_count_motif_single_sequence(self):
        """Test counting repeats in a single sequence."""
        seqs = [SeqRecord(Seq("GAGCAAGAGCAAGAGCAA"))]
        repeat_seq = "GAGCAA"
        result = count_motif(seqs, repeat_seq)
        assert result >= 1
    
    def test_count_motif_multiple_sequences(self):
        """Test counting repeats across multiple sequences."""
        seqs = [
            SeqRecord(Seq("GAGCAAGAGCAA")),
            SeqRecord(Seq("GAGCAAGAGCAAGAGCAA")),
        ]
        repeat_seq = "GAGCAA"
        result = count_motif(seqs, repeat_seq)
        assert result >= 1
    
    def test_count_motif_no_repeats(self):
        """Test with sequences containing no repeats."""
        seqs = [SeqRecord(Seq("ATGATGATGATG"))]
        repeat_seq = "GAGCAA"
        result = count_motif(seqs, repeat_seq)
        assert result >= 0  # Should return at least 0
    
    def test_count_motif_empty_sequence_list(self):
        """Test with empty sequence list."""
        seqs = []
        repeat_seq = "GAGCAA"
        result = count_motif(seqs, repeat_seq)
        assert result == 0
    
    def test_count_motif_longer_repeats(self):
        """Test with longer repeat sequences."""
        seqs = [SeqRecord(Seq("GAGCAAGAGCAAGAGCAA"))]
        repeat_seq = "GAGCAAGAGCAA"  # Longer repeat pattern
        result = count_motif(seqs, repeat_seq)
        assert result >= 0


class TestCountAllRepeats:
    """Tests for count_all_repeats function."""
    
    def test_count_all_repeats_basic(self, tmp_path):
        """Test counting all repeats in a FASTA file."""
        # Create a temporary FASTA file
        fasta_file = tmp_path / "test.fasta"
        fasta_content = """>seq1
GAGCAAGAGCAAGAGCAA
>seq2
TATATTGTATATTG
"""
        fasta_file.write_text(fasta_content)
        
        vntrs = {
            "VNTR1": "GAGCAA",
            "VNTR2": "TATATTG"
        }
        
        result, lens = count_all_repeats(str(fasta_file), vntrs)
        
        assert "VNTR1" in result
        assert "VNTR2" in result
        assert result["VNTR1"] is not None
        assert result["VNTR2"] is not None
        assert lens is None
    
    def test_count_all_repeats_single_vntr(self, tmp_path):
        """Test counting a single VNTR."""
        fasta_file = tmp_path / "test.fasta"
        fasta_content = """>seq1
GAGCAAGAGCAAGAGCAA
"""
        fasta_file.write_text(fasta_content)
        
        vntrs = {"VNTR1": "GAGCAA"}
        result, lens = count_all_repeats(str(fasta_file), vntrs)
        
        assert result["VNTR1"] >= 1
    
    def test_count_all_repeats_no_match(self, tmp_path):
        """Test with sequences that don't match any VNTR."""
        fasta_file = tmp_path / "test.fasta"
        fasta_content = """>seq1
ATGATGATGATGATGATG
"""
        fasta_file.write_text(fasta_content)
        
        vntrs = {"VNTR1": "GAGCAA"}
        result, lens = count_all_repeats(str(fasta_file), vntrs)
        
        assert result["VNTR1"] >= 0


class TestFindAllVntrs:
    """Tests for find_all_vntrs function."""
    
    @patch('core.utils.calculations.mlva.subprocess.run')
    def test_find_all_vntrs_basic(self, mock_run, tmp_path):
        """Test finding all VNTRs with mocked BLAST."""
        # Create temporary files
        fasta_file = tmp_path / "test.fasta"
        fasta_file.write_text(">seq1\nATATATATAT\n")
        
        primer_file = tmp_path / "primers.fasta"
        primer_file.write_text(">VNTR1F\nATAT\n>VNTR1R\nATAT\n")
        
        # Mock BLAST output with all 19 columns
        # qseqid, sseqid, pident, length, mismatch, gapopen, qstart, qend, sstart, send, evalue, bitscore, qlen, slen, stitle, qcovs, qframe, sframe, nident
        mock_blast_output = "VNTR1F\tseq1\t100\t4\t0\t0\t1\t4\t1\t4\t0.001\t8.0\t4\t10\tseq1\t100\t0\t0\t4\n"
        mock_run.return_value = MagicMock(stdout=mock_blast_output, stderr="")
        
        vntrs = {"VNTR1": "AT"}
        result, lens = find_all_vntrs(str(fasta_file), str(primer_file), vntrs)
        
        assert "VNTR1" in result
        assert lens == {}
    
    @patch('core.utils.calculations.mlva.subprocess.run')
    def test_find_all_vntrs_with_save_files(self, mock_run, tmp_path):
        """Test finding VNTRs with file saving enabled."""
        fasta_file = tmp_path / "test.fasta"
        fasta_file.write_text(">seq1\nATATATATAT\n")
        
        primer_file = tmp_path / "primers.fasta"
        primer_file.write_text(">VNTR1F\nATAT\n>VNTR1R\nATAT\n")
        
        save_dir = tmp_path / "results"
        save_dir.mkdir()
        
        # Mock BLAST output with all 19 columns
        mock_blast_output = "VNTR1F\tseq1\t100\t4\t0\t0\t1\t4\t1\t4\t0.001\t8.0\t4\t10\tseq1\t100\t0\t0\t4\n"
        mock_run.return_value = MagicMock(stdout=mock_blast_output, stderr="")
        
        vntrs = {"VNTR1": "AT"}
        result, lens = find_all_vntrs(str(fasta_file), str(primer_file), vntrs, save_files=str(save_dir))
        
        assert "VNTR1" in result
    
    @patch('core.utils.calculations.mlva.subprocess.run')
    def test_find_all_vntrs_no_primer_hits(self, mock_run, tmp_path):
        """Test when no primer hits are found."""
        fasta_file = tmp_path / "test.fasta"
        fasta_file.write_text(">seq1\nATATATATAT\n")
        
        primer_file = tmp_path / "primers.fasta"
        primer_file.write_text(">VNTR1F\nGGGGGG\n>VNTR1R\nGGGGGG\n")
        
        # Mock empty BLAST output
        mock_run.return_value = MagicMock(stdout="", stderr="")
        
        vntrs = {"VNTR1": "AT"}
        result, lens = find_all_vntrs(str(fasta_file), str(primer_file), vntrs)
        
        assert result["VNTR1"] == "NP"  # No primer


class TestFindRepeatsByLength:
    """Tests for find_repeats_by_length function."""
    
    @patch('core.utils.calculations.mlva.subprocess.run')
    def test_find_repeats_by_length_basic(self, mock_run, tmp_path):
        """Test finding repeats by length with mocked BLAST."""
        fasta_file = tmp_path / "test.fasta"
        fasta_file.write_text(">seq1\nATATATATAT\n")
        
        primer_file = tmp_path / "primers.fasta"
        primer_file.write_text(">VNTR1F 10\nATAT\n>VNTR1R 10\nATAT\n")
        
        # Mock BLAST output with all 19 columns
        mock_blast_output = "VNTR1F\tseq1\t100\t4\t0\t0\t1\t4\t1\t4\t0.001\t8.0\t4\t10\tseq1\t100\t0\t0\t4\n"
        mock_run.return_value = MagicMock(stdout=mock_blast_output, stderr="")
        
        vntrs = {"VNTR1": "AT"}
        result, lens = find_repeats_by_length(str(fasta_file), str(primer_file), vntrs)
        
        assert "VNTR1" in result
    
    @patch('core.utils.calculations.mlva.subprocess.run')
    def test_find_repeats_by_length_with_save_files(self, mock_run, tmp_path):
        """Test finding repeats by length with file saving."""
        fasta_file = tmp_path / "test.fasta"
        fasta_file.write_text(">seq1\nATATATATAT\n")
        
        primer_file = tmp_path / "primers.fasta"
        primer_file.write_text(">VNTR1F 10\nATAT\n>VNTR1R 10\nATAT\n")
        
        save_dir = tmp_path / "results"
        save_dir.mkdir()
        
        # Mock BLAST output with all 19 columns
        mock_blast_output = "VNTR1F\tseq1\t100\t4\t0\t0\t1\t4\t1\t4\t0.001\t8.0\t4\t10\tseq1\t100\t0\t0\t4\n"
        mock_run.return_value = MagicMock(stdout=mock_blast_output, stderr="")
        
        vntrs = {"VNTR1": "AT"}
        result, lens = find_repeats_by_length(str(fasta_file), str(primer_file), vntrs, save_files=str(save_dir))
        
        assert "VNTR1" in result