"""MLVA analysis module for analyzing Variable Number Tandem Repeats (VNTRs) in DNA sequences.

This module provides functionality for:
- Finding repeat sequences in DNA
- Counting VNTRs using BLAST
- Analyzing repeat motifs
"""

import argparse
import sys
import os
from io import StringIO
from pathlib import Path
import csv
import regex
import subprocess
from typing import Dict, Optional, Tuple, List, Union

from Bio import SeqIO, SearchIO
from Bio.SeqRecord import SeqRecord

# BLAST configuration constants
DEFAULT_OUTFMT = "6 std qlen slen stitle qcovs qframe sframe nident"
DEFAULT_FIELDS = [
    "qseqid", "sseqid", "pident", "length", "mismatch", "gapopen",
    "qstart", "qend", "sstart", "send", "evalue", "bitscore",
]
EXTRA_FIELDS = ["qlen", "slen", "stitle", "qcovs", "qframe", "sframe", "nident"]

# Regex pattern constants
MAX_FUZZY_ERRORS = 1
ERRORS_PER_REPEAT = 1

# Result codes
NO_PRIMER_HIT = "NP"
DIFFERENT_CONTIGS = "DC"
DEGENERATE_BASES_FACTOR = 3


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for MLVA analysis.

    Returns:
        argparse.Namespace: Parsed arguments with fastas, primer_fasta,
            output, verbose_output, and count.
    """
    parser = argparse.ArgumentParser(
        description="Analyze Variable Number Tandem Repeats (VNTRs) in DNA sequences"
    )
    parser.add_argument("-f", "--fastas", nargs="+", required=True,
                        help="FASTA files or file containing list of paths")
    parser.add_argument("-v", "--primer_fasta", required=True,
                        help="FASTA file with primer sequences")
    parser.add_argument("-o", "--output", default=None,
                        help="Output CSV file path")
    parser.add_argument("-i", "--verbose_output", default=None,
                        help="Directory for verbose output files")
    parser.add_argument("-c", "--count", default=False, action="store_true",
                        help="Count exact repeats instead of calculating by length")

    return parser.parse_args()


def find_repeats(seq: SeqRecord, repeat_seq: str, max_errors: int = MAX_FUZZY_ERRORS) -> int:
    """Find the count of repeat sequences in a DNA sequence.

    Uses fuzzy regex matching to find repeats, allowing for errors.
    If no matches in forward strand, checks reverse complement.

    Args:
        seq: SeqRecord object containing the DNA sequence
        repeat_seq: The repeat sequence pattern to search for
        max_errors: Maximum errors allowed per repeat (default: 1)

    Returns:
        int: Count of repeats found
    """
    pattern = f"({repeat_seq}){{e<={max_errors}}}"
    repeats = regex.findall(pattern, str(seq.seq), flags=regex.IGNORECASE)

    if len(repeats) == 0:
        repeats = regex.findall(
            pattern, str(seq.reverse_complement().seq), flags=regex.IGNORECASE
        )

    return len(repeats)


def _run_blast(primer_file: str, fasta_file: str) -> Tuple[Dict, str]:
    """Execute BLAST search and parse results.

    Args:
        primer_file: Path to primer FASTA file
        fasta_file: Path to subject FASTA file

    Returns:
        Tuple of (primer_hits dict, raw blast output string)
    """
    blast_cmd = [
        "blastn", "-task", "blastn-short",
        "-query", primer_file, "-subject", fasta_file,
        "-outfmt", DEFAULT_OUTFMT,
    ]

    result = subprocess.run(blast_cmd, capture_output=True, text=True, check=True)
    bf = StringIO(result.stdout)
    blast_results = SearchIO.parse(
        bf, "blast-tab", fields=DEFAULT_FIELDS + EXTRA_FIELDS
    )

    best_primer_hits = {}
    for qr in blast_results:
        best_primer_hits[qr.id] = qr if qr and len(qr) > 0 and len(qr[0]) > 0 else None

    return best_primer_hits, result.stdout


def _extract_filename(filepath: str) -> str:
    """Extract filename without extension from filepath.

    Args:
        filepath: Path to file

    Returns:
        Filename without extension
    """
    return Path(filepath).stem


def _save_blast_results(
    blast_output: str,
    primer_hits: Dict,
    output_dir: str,
    filename: str
) -> None:
    """Save BLAST results to TSV files.

    Args:
        blast_output: Raw BLAST output string
        primer_hits: Dictionary of primer hits from BLAST
        output_dir: Output directory path
        filename: Base filename for output files
    """
    output_path = os.path.join(output_dir, f"{filename}_primer_blast.tsv")
    with open(output_path, "w") as f:
        f.write("\t".join(DEFAULT_FIELDS + EXTRA_FIELDS) + "\n")
        SearchIO.write(
            [qr.hsp_filter(lambda hit: hit == qr[0][0])
             for qr in primer_hits.values() if qr],
            f, "blast-tab",
            fields=DEFAULT_FIELDS + EXTRA_FIELDS,
        )

    raw_output_path = os.path.join(output_dir, f"{filename}_raw_primer_blast.tsv")
    with open(raw_output_path, "w") as f:
        f.write(blast_output)


def _get_primer_hit(primer_hits: Dict, vntr_id: str, strand: str):
    """Get primer hit for a specific VNTR and strand.

    Args:
        primer_hits: Dictionary of primer hits from BLAST
        vntr_id: VNTR identifier
        strand: Strand indicator ("F" for forward, "R" for reverse)

    Returns:
        Hit object or None if not found
    """
    primer_key = f"{vntr_id}{strand}"
    if primer_key in primer_hits and primer_hits[primer_key]:
        return primer_hits[primer_key][0][0]
    return None


def _process_vntr_hits(
    vntr: str,
    forward_hit,
    reverse_hit,
    fasta_seqs,
    repeat_seq: str,
    data: Dict,
    amp_seqs: List,
    lens: Dict,
    save_files: Optional[str],
    filename: str
) -> None:
    """Process VNTR hits from primer matches and count repeats.

    Args:
        vntr: VNTR identifier
        forward_hit: Forward primer hit object
        reverse_hit: Reverse primer hit object
        fasta_seqs: Indexed FASTA sequences
        repeat_seq: Repeat sequence pattern
        data: Dictionary to store results
        amp_seqs: List to accumulate amplified sequences
        lens: Dictionary to store sequence lengths
        save_files: Optional output directory
        filename: Base filename for outputs
    """
    forward_contig = forward_hit.hit_id
    reverse_contig = reverse_hit.hit_id

    # Handle primers on different contigs
    if reverse_contig != forward_contig:
        fs, fe = tuple(sorted([forward_hit.hit_start, forward_hit.hit_end]))
        forward_seq = (
            fasta_seqs[forward_contig][fs:]
            if forward_hit.hit_strand > 0
            else fasta_seqs[forward_contig][:fe]
        )
        rs, re = tuple(sorted([reverse_hit.hit_start, reverse_hit.hit_end]))
        reverse_seq = (
            fasta_seqs[reverse_contig][rs:]
            if reverse_hit.hit_strand > 0
            else fasta_seqs[reverse_contig][:rs]
        )
        f_repeats = find_repeats(forward_seq, repeat_seq)
        r_repeats = find_repeats(reverse_seq, repeat_seq)
        data[vntr] = f">={max(f_repeats, r_repeats)}"
        return

    # Extract amplified region between primers
    forward_start, forward_end = forward_hit.hit_start, forward_hit.hit_end
    reverse_start, reverse_end = reverse_hit.hit_start, reverse_hit.hit_end
    min_pos = min(forward_start, forward_end, reverse_start, reverse_end)
    max_pos = max(forward_start, forward_end, reverse_start, reverse_end)

    seq = fasta_seqs[forward_contig][min_pos:max_pos]
    data[vntr] = find_repeats(seq, repeat_seq)

    # Optionally save sequence information
    if save_files:
        seq.id = f"{forward_contig}_{forward_start}_{reverse_end}_{vntr}"
        lens[vntr] = len(seq)
        amp_seqs.append(seq)


def find_all_vntrs(
    fasta_file: str,
    primer_file: str,
    vntrs: Dict[str, str],
    save_files: Optional[str] = None
) -> Tuple[Dict[str, Union[int, str]], Dict]:
    """Find and count all VNTRs in a FASTA file using BLAST primer matching.

    Performs BLAST search with primers and counts repeats in the amplified regions.
    Handles cases where primers are on different contigs or missing.

    Args:
        fasta_file: Path to FASTA file to analyze
        primer_file: Path to primer FASTA file
        vntrs: Dictionary mapping VNTR names to repeat sequences
        save_files: Optional directory path to save intermediate BLAST results

    Returns:
        Tuple containing (results dict, lengths dict)
    """
    primer_hits, blast_output = _run_blast(primer_file, fasta_file)

    data = {v: None for v in vntrs}
    fasta_seqs = SeqIO.index(fasta_file, "fasta")
    filename = _extract_filename(fasta_file)

    # Save BLAST results if requested
    if save_files:
        _save_blast_results(blast_output, primer_hits, save_files, filename)

    amp_seqs = []
    lens = {}

    for vntr, repeat in vntrs.items():
        forward_hit = _get_primer_hit(primer_hits, vntr, "F")
        reverse_hit = _get_primer_hit(primer_hits, vntr, "R")

        # Handle missing primer hits
        if not forward_hit or not reverse_hit:
            data[vntr] = NO_PRIMER_HIT
            continue

        # Extract sequences and count repeats
        _process_vntr_hits(
            vntr, forward_hit, reverse_hit, fasta_seqs, repeat, data,
            amp_seqs, lens, save_files, filename
        )

    # Save amplified sequences if requested
    if save_files and amp_seqs:
        SeqIO.write(
            amp_seqs,
            os.path.join(save_files, f"{filename}_seqs.fasta"),
            "fasta"
        )

    return data, lens


def count_motif(seqs: List[SeqRecord], repeat_seq: str) -> int:
    """Count the maximum number of tandem repeats across sequences.

    Searches for increasingly longer repeat patterns with fuzzy matching.

    Args:
        seqs: List of SeqRecord objects to analyze
        repeat_seq: The repeat sequence pattern to search for

    Returns:
        int: Maximum number of repeats found
    """
    max_num_repeats = 0

    for s in seqs:
        i = 0
        found = True

        while found:
            motif_pattern = r"(%s){e<=%s}" % (
                repeat_seq * (i + 1),
                str(ERRORS_PER_REPEAT * (i + 1)),
            )
            forward_hits = regex.findall(
                motif_pattern, str(s.seq), flags=regex.IGNORECASE
            )
            reverse_hits = regex.findall(
                motif_pattern, str(s.seq.reverse_complement()), flags=regex.IGNORECASE
            )
            found = bool(forward_hits or reverse_hits)
            i += 1

        if i > max_num_repeats:
            max_num_repeats = i

    return max_num_repeats


def count_all_repeats(
    fasta_file: str,
    vntrs: Dict[str, str]
) -> Tuple[Dict[str, int], None]:
    """Count all repeats in FASTA file for multiple VNTRs.

    Args:
        fasta_file: Path to FASTA file to analyze
        vntrs: Dictionary mapping VNTR names to repeat sequences

    Returns:
        Tuple containing (results dict, None)
    """
    seqs = list(SeqIO.parse(fasta_file, "fasta"))
    data = {v: None for v in vntrs}

    for v, r in vntrs.items():
        data[v] = count_motif(seqs, r)

    return data, None


def _calculate_repeat_count_by_length(
    seq_length: int,
    primer_length: int,
    repeat_seq: str
) -> int:
    """Calculate repeat count from amplified sequence length.

    Args:
        seq_length: Length of amplified sequence
        primer_length: Length of primer region
        repeat_seq: Repeat sequence (may contain degenerate bases)

    Returns:
        Count of repeats calculated from length
    """
    repeat_len = len(repeat_seq) - repeat_seq.count("[") * DEGENERATE_BASES_FACTOR
    return round((seq_length - primer_length) / repeat_len)


def _extract_vntr_lengths(primer_file: str) -> Dict[str, int]:
    """Extract VNTR lengths from primer FASTA file descriptions.

    Args:
        primer_file: Path to primer FASTA file

    Returns:
        Dictionary mapping VNTR names to their lengths
    """
    vntr_lens = {}
    for s in SeqIO.parse(primer_file, "fasta"):
        vntr_id = s.id[:-1]  # Remove F/R suffix
        vntr_lens[vntr_id] = int(s.description.split()[-1])
    return vntr_lens


def _process_vntr_hits_by_length(
    vntr: str,
    forward_hit,
    reverse_hit,
    fasta_seqs,
    repeat_seq: str,
    vntr_lens: Dict,
    data: Dict,
    amp_seqs: List,
    lens: Dict,
    save_files: Optional[str],
    filename: str
) -> None:
    """Process VNTR hits and calculate repeats by length.

    Args:
        vntr: VNTR identifier
        forward_hit: Forward primer hit object
        reverse_hit: Reverse primer hit object
        fasta_seqs: Indexed FASTA sequences
        repeat_seq: Repeat sequence pattern
        vntr_lens: Dictionary of VNTR lengths
        data: Dictionary to store results
        amp_seqs: List to accumulate amplified sequences
        lens: Dictionary to store sequence lengths
        save_files: Optional output directory
        filename: Base filename for outputs
    """
    forward_contig = forward_hit.hit_id
    reverse_contig = reverse_hit.hit_id

    # Handle primers on different contigs
    if reverse_contig != forward_contig:
        data[vntr] = DIFFERENT_CONTIGS
        return

    # Extract amplified region between primers
    forward_start, forward_end = forward_hit.hit_start, forward_hit.hit_end
    reverse_start, reverse_end = reverse_hit.hit_start, reverse_hit.hit_end
    min_pos = min(forward_start, forward_end, reverse_start, reverse_end)
    max_pos = max(forward_start, forward_end, reverse_start, reverse_end)

    seq = fasta_seqs[forward_contig][min_pos:max_pos]
    data[vntr] = _calculate_repeat_count_by_length(
        len(seq), vntr_lens[vntr], repeat_seq
    )

    # Optionally save sequence information
    if save_files:
        seq.id = f"{forward_contig}_{forward_start}_{reverse_end}__{vntr}"
        lens[vntr] = len(seq)
        amp_seqs.append(seq)


def find_repeats_by_length(
    fasta_file: str,
    primer_file: str,
    vntrs: Dict[str, str],
    save_files: Optional[str] = None
) -> Tuple[Dict[str, Union[int, str]], Dict]:
    """Find repeats by calculating based on amplified sequence length.

    This method is faster than counting repeats directly, as it only requires
    the total length of the amplified region.

    Args:
        fasta_file: Path to FASTA file to analyze
        primer_file: Path to primer FASTA file
        vntrs: Dictionary mapping VNTR names to repeat sequences
        save_files: Optional directory path to save intermediate BLAST results

    Returns:
        Tuple containing (results dict, lengths dict)
    """
    primer_hits, blast_output = _run_blast(primer_file, fasta_file)
    vntr_lens = _extract_vntr_lengths(primer_file)

    data = {v: None for v in vntrs}
    fasta_seqs = SeqIO.index(fasta_file, "fasta")
    filename = _extract_filename(fasta_file)

    # Save BLAST results if requested
    if save_files:
        _save_blast_results(blast_output, primer_hits, save_files, filename)

    amp_seqs = []
    lens = {}

    for vntr, repeat in vntrs.items():
        forward_hit = _get_primer_hit(primer_hits, vntr, "F")
        reverse_hit = _get_primer_hit(primer_hits, vntr, "R")

        # Handle missing primer hits
        if not forward_hit or not reverse_hit:
            data[vntr] = NO_PRIMER_HIT
            continue

        # Extract sequences and calculate repeat count by length
        _process_vntr_hits_by_length(
            vntr, forward_hit, reverse_hit, fasta_seqs, repeat,
            vntr_lens, data, amp_seqs, lens, save_files, filename
        )

    # Save amplified sequences if requested
    if save_files and amp_seqs:
        SeqIO.write(
            amp_seqs,
            os.path.join(save_files, f"{filename}_seqs.fasta"),
            "fasta"
        )

    return data, lens


def _load_fasta_files(fasta_input: List[str]) -> List[str]:
    """Load FASTA file paths from list or file.

    Args:
        fasta_input: Either list of FASTA files or path to file containing list

    Returns:
        List of FASTA file paths
    """
    if len(fasta_input) == 1 and os.path.splitext(fasta_input[0])[1][1:] not in [
        "fasta", "faa", "fa", "fsa", "fna",
    ]:
        # First argument is a file containing list of paths
        fasta_files = []
        with open(fasta_input[0], "r") as f:
            for line in f:
                fasta_files.append(line.strip())
        return fasta_files
    return fasta_input


def _load_vntrs(primer_file: str) -> Dict[str, str]:
    """Load VNTR definitions from primer FASTA file.

    Args:
        primer_file: Path to primer FASTA file

    Returns:
        Dictionary mapping VNTR names to repeat sequences
    """
    vntrs = {}
    for p in SeqIO.parse(primer_file, "fasta"):
        vntr_name = p.id[:-1]  # Remove F/R suffix
        vntrs[vntr_name] = p.description.split()[1].strip()
    return vntrs


def main() -> None:
    """Main entry point for MLVA analysis."""
    args = parse_args()

    fasta_files = _load_fasta_files(args.fastas)
    vntrs = _load_vntrs(args.primer_fasta)
    verbose = args.verbose_output or False

    vntr_data = {}
    lens = {}

    for fasta_file in fasta_files:
        filename = _extract_filename(fasta_file)

        if args.count:
            vntr_data[filename], lens[filename] = find_all_vntrs(
                fasta_file, args.primer_fasta, vntrs, save_files=verbose
            )
        else:
            vntr_data[filename], lens[filename] = find_repeats_by_length(
                fasta_file, args.primer_fasta, vntrs, save_files=verbose
            )

    # Write results to output
    output = open(args.output, "w") if args.output else sys.stdout
    writer = csv.DictWriter(output, ["Isolate"] + list(vntrs.keys()))
    writer.writeheader()

    for filename, data in vntr_data.items():
        data["Isolate"] = filename
        writer.writerow(data)

    # Write length information if verbose
    if verbose:
        with open(os.path.join(verbose, "vntr_lengths.csv"), "w") as f:
            writer = csv.DictWriter(f, ["Isolate"] + list(vntrs.keys()))
            writer.writeheader()
            for filename, length_data in lens.items():
                length_data["Isolate"] = filename
                writer.writerow(length_data)


if __name__ == "__main__":
    main()
