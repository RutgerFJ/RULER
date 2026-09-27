"""
Management command to initialize LocusSet and Locus objects from primer FASTA files.
"""

import logging
from pathlib import Path

from Bio import SeqIO

from django.core.management.base import BaseCommand

from core.models import LocusSet, Locus


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Initialize LocusSet and Locus objects from primer FASTA file'

    def add_arguments(self, parser):
        parser.add_argument(
            '--primer-file',
            type=str,
            default='core/utils/calculations/cd_primers.fasta',
            help='Path to primer FASTA file (default: core/utils/calculations/cd_primers.fasta)'
        )
        parser.add_argument(
            '--locus-set-name',
            type=str,
            default='cd_primers',
            help='Name of the LocusSet to create (default: cd_primers)'
        )
        parser.add_argument(
            '--description',
            type=str,
            default='C. difficile MLVA Locus Set',
            help='Description of the LocusSet'
        )

    def handle(self, *args, **options):
        primer_file = Path(options['primer_file'])
        locus_set_name = options['locus_set_name']
        description = options['description']

        if not primer_file.exists():
            self.stdout.write(
                self.style.ERROR(f'Primer file not found: {primer_file}')
            )
            return

        self.stdout.write(f'Loading primers from {primer_file}...')

        # Get or create LocusSet
        locus_set, created = LocusSet.objects.get_or_create(
            name=locus_set_name,
            defaults={'description': description}
        )

        if created:
            self.stdout.write(
                self.style.SUCCESS(f'Created LocusSet: {locus_set_name}')
            )
        else:
            self.stdout.write(
                self.style.WARNING(f'LocusSet already exists: {locus_set_name}')
            )

        # Parse primer file and create Locus objects
        loci_data = {}  # {locus_name: {'forward': seq, 'reverse': seq, 'repeat': str, 'length': int}}
        
        for record in SeqIO.parse(str(primer_file), 'fasta'):
            # Parse header: ">CDR4F GAGCAA   105"
            # Format: LOCUSNAME + F/R  REPEAT_SEQUENCE  EXPECTED_LENGTH
            parts = record.description.split()
            locus_id = parts[0]  # e.g., "CDR4F"
            
            # Extract locus name (remove F/R suffix)
            locus_name = locus_id.rstrip('FR')
            repeat_seq = parts[1] if len(parts) > 1 else ''
            expected_length = parts[2] if len(parts) > 2 else ''
            
            # Determine if forward or reverse
            direction = 'F' if locus_id.endswith('F') else 'R'
            
            # Initialize locus data if not exists
            if locus_name not in loci_data:
                loci_data[locus_name] = {
                    'forward': '',
                    'reverse': '',
                    'repeat': repeat_seq,
                    'length': expected_length
                }
            
            # Store primer sequence
            if direction == 'F':
                loci_data[locus_name]['forward'] = str(record.seq)
            else:
                loci_data[locus_name]['reverse'] = str(record.seq)

        # Create or update Locus objects
        created_count = 0
        updated_count = 0

        for locus_name, data in sorted(loci_data.items()):
            locus, created = Locus.objects.get_or_create(
                locus_set=locus_set,
                name=locus_name,
                defaults={
                    'forward_primer': data['forward'],
                    'reverse_primer': data['reverse'],
                    'repeat_sequence': data['repeat']
                }
            )
            
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  ✓ Created Locus: {locus_name}')
                )
            else:
                # Update existing locus with new primer data
                locus.forward_primer = data['forward']
                locus.reverse_primer = data['reverse']
                locus.repeat_sequence = data['repeat']
                locus.save()
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(f'  ⚠ Updated Locus: {locus_name}')
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✓ Initialization complete:\n'
                f'  - Created {created_count} Locus objects\n'
                f'  - Updated {updated_count} Locus objects\n'
                f'  - Total Loci in set: {locus_set.loci.count()}'
            )
        )
