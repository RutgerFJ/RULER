import argparse
import sys
import csv
import itertools
from typing import Dict, List, Tuple, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--skip_loci", nargs="+", default=None)
    return parser.parse_args()


def read_mlva_data(input_file: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Read MLVA profiles from a CSV file.
    
    Args:
        input_file: Path to the input CSV file.
        
    Returns:
        Tuple of (mlva_data dict, loci list) where mlva_data maps isolate IDs to their MLVA values.
    """
    mlva_data = {}
    loci = []
    with open(input_file, "r") as f:
        reader = csv.reader(f, delimiter=",")
        header = next(reader)
        loci = header[1:]  # Skip first column (isolate ID)

        for row in reader:
            isolate_id = row[0]
            values = row[1:]
            mlva_data[isolate_id] = values
    
    return mlva_data, loci


def calculate_strd_values(
    mlva_data: Dict[str, List[str]], loci: List[str], keep_loci: List[str]
) -> List[List]:
    """Calculate STRD for all isolate pairs.
    
    Args:
        mlva_data: Dictionary mapping isolate IDs to MLVA values.
        loci: List of all locus names.
        keep_loci: List of loci to include in calculation (excludes skipped loci).
        
    Returns:
        List of [isolate1, isolate2, strd, lv] values for all pairwise comparisons.
    """
    strds = []
    for i1, i2 in itertools.combinations(mlva_data.keys(), 2):
        r1 = mlva_data[i1]
        r2 = mlva_data[i2]
        strd = 0
        lv = 0

        for idx, l in enumerate(loci):
            if l not in keep_loci:
                continue

            l1_val = r1[idx]
            l2_val = r2[idx]

            if l1_val == "DC" or l2_val == "DC":
                continue

            try:
                l1 = int(l1_val)
                l2 = int(l2_val)
            except ValueError:
                continue

            if l1 != l2:
                lv += 1
                strd += abs(l1 - l2)

        strds.append([i1, i2, strd, lv])
    
    return strds


def write_output(strds: List[List], output_file: Optional[str] = None) -> None:
    """Write STRD results to a CSV file or stdout.
    
    Args:
        strds: List of [isolate1, isolate2, strd, lv] values.
        output_file: Path to output file. If None, writes to stdout.
    """
    if output_file:
        with open(output_file, "w", newline="") as f:
            writer = csv.writer(f, delimiter=",")
            writer.writerow(["Isolate1", "Isolate2", "STRD", "LV"])
            writer.writerows(strds)
    else:
        writer = csv.writer(sys.stdout)
        writer.writerow(["Isolate1", "Isolate2", "STRD", "LV"])
        writer.writerows(strds)


def main() -> None:
    """Main entry point for the script."""
    args = parse_args()
    
    mlva_data, loci = read_mlva_data(args.input)
    skip_loci = args.skip_loci if args.skip_loci else []
    keep_loci = [l for l in loci if l not in skip_loci]
    
    strds = calculate_strd_values(mlva_data, loci, keep_loci)
    write_output(strds, args.output)


if __name__ == "__main__":
    main()
