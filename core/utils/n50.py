"""
N50 calculation utility for genome assembly analysis.

N50 is a bioinformatics metric representing the length of the contig at which 
50% of the total sequence data is contained.
"""


def calculate_n50(contig_lengths: list) -> int:
    """
    Calculate N50 value from a list of contig lengths.
    
    N50 is the length of the contig at which 50% of the total sequence data 
    is contained. Contigs are sorted by length in descending order, and we 
    sum cumulatively until reaching 50% of the total length.
    
    Args:
        contig_lengths: List of integer contig lengths in base pairs
        
    Returns:
        N50 value (int) or 0 if no contigs provided
        
    Example:
        >>> calculate_n50([100, 50, 40, 10])
        100
        >>> calculate_n50([90, 80, 70, 60, 50])
        70
        >>> calculate_n50([])
        0
    """
    if not contig_lengths:
        return 0
    
    # Sort contigs by length in descending order
    sorted_lengths = sorted(contig_lengths, reverse=True)
    
    # Calculate total length
    total_length = sum(sorted_lengths)
    
    # Calculate 50% threshold
    threshold = total_length / 2
    
    # Sum cumulatively until reaching threshold
    cumulative_length = 0
    for length in sorted_lengths:
        cumulative_length += length
        if cumulative_length >= threshold:
            return length
    
    # Fallback (shouldn't reach here if input is valid)
    return 0
