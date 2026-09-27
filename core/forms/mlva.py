from django import forms
from django.core.exceptions import ValidationError

from core.models import LocusSet, Locus


class MLVAConfigPrimerSelectionForm(forms.Form):
    """
    Step 1: Select primer set (existing or create new).
    Allows choosing from database, uploading custom FASTA primers, or creating a new named primer set.
    """
    SELECTION_CHOICES = [
        ('existing', 'Use existing primer set from database'),
        ('create_new', 'Create and use new primer set'),
    ]
    
    selection_method = forms.ChoiceField(
        required=True,
        choices=SELECTION_CHOICES,
        label="Primer Set Selection",
        widget=forms.RadioSelect(attrs={
            'class': 'radio',
        })
    )
    
    existing_primer_set = forms.ModelChoiceField(
        required=False,
        queryset=None,
        label="Select Primer Set",
        empty_label="-- Choose a primer set --",
        widget=forms.Select(attrs={
            'class': 'input input-bordered bg-base-100',
        })
    )
    
    primer_fasta_file = forms.FileField(
        required=False,
        label="Primer FASTA File",
        help_text="Upload a FASTA file with primer sets (format: >LocusName RepeatSeq with forward/reverse primers)",
        widget=forms.FileInput(attrs={
            'class': 'file-input file-input-primary bg-base-100',
            'accept': '.fasta,.fa,.txt',
        })
    )
    
    # Fields for creating new locus set
    new_locus_set_name = forms.CharField(
        max_length=20,
        required=False,
        label="Locus Set Name",
        widget=forms.TextInput(attrs={
            'class': 'input input-bordered w-full bg-base-100',
            'placeholder': 'e.g., CD_MLVA_v1',
        }),
        help_text="A short name for this primer set (max 20 characters)"
    )
    
    new_locus_set_description = forms.CharField(
        max_length=200,
        required=False,
        label="Description",
        widget=forms.Textarea(attrs={
            'class': 'textarea textarea-bordered w-full bg-base-100 pt-3 leading-normal',
            'placeholder': 'e.g., Clostridium difficile MLVA scheme v1.0',
            'rows': 2,
        }),
        help_text="Describe the primer set (max 200 characters)"
    )
    
    new_locus_set_primers = forms.FileField(
        required=False,
        label="Primer FASTA File",
        help_text="Upload a FASTA file with primer sets",
        widget=forms.FileInput(attrs={
            'class': 'file-input file-input-primary bg-base-100',
            'accept': '.fasta,.fa,.txt',
        })
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate existing primer sets from database
        self.fields['existing_primer_set'].queryset = LocusSet.objects.all().order_by('name')
    
    def clean(self):
        """Validate selection method and corresponding field."""
        cleaned_data = super().clean()
        selection_method = cleaned_data.get('selection_method')
        existing_primer_set = cleaned_data.get('existing_primer_set')
        primer_fasta_file = cleaned_data.get('primer_fasta_file')
        new_locus_set_name = cleaned_data.get('new_locus_set_name')
        new_locus_set_description = cleaned_data.get('new_locus_set_description')
        new_locus_set_primers = cleaned_data.get('new_locus_set_primers')
        
        if selection_method == 'existing':
            if not existing_primer_set:
                self.add_error('existing_primer_set', 'Please select a primer set from the database.')
        elif selection_method == 'upload':
            # Only add "no file" error if there's not already an error from file parsing
            if not primer_fasta_file and 'primer_fasta_file' not in self.errors:
                self.add_error('primer_fasta_file', 'Please upload a primer FASTA file.')
        elif selection_method == 'create_new':
            if not new_locus_set_name:
                self.add_error('new_locus_set_name', 'Please enter a locus set name.')
            if not new_locus_set_description:
                self.add_error('new_locus_set_description', 'Please enter a description.')
            if not new_locus_set_primers and 'new_locus_set_primers' not in self.errors:
                self.add_error('new_locus_set_primers', 'Please upload a primer FASTA file.')

        return cleaned_data
    
    def clean_new_locus_set_name(self):
        """Validate new locus set name."""
        selection_method = self.cleaned_data.get('selection_method')
        name = self.cleaned_data.get('new_locus_set_name', '').strip()
        
        if selection_method == 'create_new':
            if not name:
                raise ValidationError("Locus set name cannot be empty.")
            
            # Check if name already exists
            if LocusSet.objects.filter(name=name).exists():
                raise ValidationError(f"A locus set named '{name}' already exists.")
        
        return name
    
    def clean_new_locus_set_description(self):
        """Validate new locus set description."""
        selection_method = self.cleaned_data.get('selection_method')
        description = self.cleaned_data.get('new_locus_set_description', '').strip()
        
        if selection_method == 'create_new':
            if not description:
                raise ValidationError("Description cannot be empty.")
        
        return description
    
    def clean_new_locus_set_primers(self):
        """Parse and validate new locus set primer FASTA file."""
        selection_method = self.cleaned_data.get('selection_method')
        primer_file = self.cleaned_data.get('new_locus_set_primers')
        
        if selection_method != 'create_new':
            return primer_file
        
        if not primer_file:
            raise ValidationError("Primer file is required.")
        
        # Check file size
        if primer_file.size > 5 * 1024 * 1024:  # 5 MB
            raise ValidationError("Primer file is too large (max 5 MB).")
        
        # Read and parse file
        try:
            content = primer_file.read().decode('utf-8')
            parsed_primers = self._parse_primer_fasta(content)
            
            if not parsed_primers:
                raise ValidationError("Primer file contains no valid loci.")
            
            # Store parsed data for later retrieval
            self.parsed_primers_new = parsed_primers
            
        except UnicodeDecodeError:
            raise ValidationError("Primer file is not valid UTF-8 text.")
        except ValueError as e:
            raise ValidationError(f"Error parsing primer file: {str(e)}")
        
        return primer_file
    
    def clean_primer_fasta_file(self):
        """Parse and validate primer FASTA file."""
        primer_file = self.cleaned_data.get('primer_fasta_file')
        
        if not primer_file:
            return primer_file
        
        # Check file size
        if primer_file.size > 5 * 1024 * 1024:  # 5 MB
            raise ValidationError("Primer file is too large (max 5 MB).")
        
        # Read and parse file
        try:
            content = primer_file.read().decode('utf-8')
            parsed_primers = self._parse_primer_fasta(content)
            
            if not parsed_primers:
                raise ValidationError("Primer file contains no valid loci.")
            
            # Store parsed data for later retrieval
            self.parsed_primers = parsed_primers
            
        except UnicodeDecodeError:
            raise ValidationError("Primer file is not valid UTF-8 text.")
        except ValueError as e:
            raise ValidationError(f"Error parsing primer file: {str(e)}")
        
        return primer_file
    
    @staticmethod
    def _parse_primer_fasta(content):
        """
        Parse MLVA primer FASTA file.
        Expected format:
          >LocusName_F RepeatSeq [Length]
          ForwardPrimerSequence
          >LocusName_R RepeatSeq [Length]
          ReversePrimerSequence
        
        Or without underscore:
          >LocusNameF RepeatSeq [Length]
          ForwardPrimerSequence
          >LocusNameR RepeatSeq [Length]
          ReversePrimerSequence
        
        Returns: {locus_name: {'repeat': seq, 'forward': seq, 'reverse': seq}, ...}
        """
        loci = {}
        current_locus = None
        current_name = None
        current_repeat = None
        current_is_forward = False
        
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('>'):
                # Parse header
                header = line[1:].strip()
                parts = header.split()
                
                if not parts:
                    raise ValueError("Empty FASTA header")
                
                name_part = parts[0]
                
                # Determine if this is forward or reverse - check for _F, _R, or just F, R at the end
                is_reverse = False
                base_name = name_part
                
                # Check for underscore format: name_F or name_R
                if '_F' in name_part or '_f' in name_part:
                    base_name = name_part.replace('_F', '').replace('_f', '')
                    is_reverse = False
                elif '_R' in name_part or '_r' in name_part:
                    base_name = name_part.replace('_R', '').replace('_r', '')
                    is_reverse = True
                # Check for non-underscore format: nameF or nameR
                elif name_part.endswith('F') or name_part.endswith('f'):
                    base_name = name_part[:-1]
                    is_reverse = False
                elif name_part.endswith('R') or name_part.endswith('r'):
                    base_name = name_part[:-1]
                    is_reverse = True
                
                # Get repeat sequence (should be in parts[1])
                repeat_seq = parts[1] if len(parts) > 1 else ''
                
                # Initialize locus if new
                if base_name not in loci:
                    loci[base_name] = {'repeat': repeat_seq, 'forward': None, 'reverse': None}
                    current_locus = base_name
                
                current_name = name_part
                current_repeat = repeat_seq
                current_is_forward = not is_reverse
            else:
                # This is primer sequence
                if current_locus is None:
                    raise ValueError("Sequence without header")
                
                primer_seq = line.upper()
                
                # Validate sequence (only ATGC and N allowed)
                if not all(c in 'ATGCN' for c in primer_seq):
                    raise ValueError(f"Invalid character in primer sequence: {primer_seq}")
                
                # Store in appropriate field
                if current_is_forward:
                    loci[current_locus]['forward'] = primer_seq
                else:
                    loci[current_locus]['reverse'] = primer_seq
        
        # Validate all loci have both forward and reverse
        for locus_name, locus_data in loci.items():
            if not locus_data['forward'] or not locus_data['reverse']:
                raise ValueError(
                    f"Locus '{locus_name}' missing forward or reverse primer. "
                    f"Each locus must have both F and R primers."
                )
        
        return loci

class MLVAConfigCountModeForm(forms.Form):
    """
    Step 2: Select count mode for MLVA analysis.
    """
    COUNT_MODE_CHOICES = [
        ('uncounted', 'Uncounted - Calculate repeats based on length'),
        ('counted', 'Counted - Count repeats'),
    ]
    
    count_mode = forms.ChoiceField(
        required=True,
        choices=COUNT_MODE_CHOICES,
        label="Repeat Counting Mode",
        widget=forms.RadioSelect(attrs={
            'class': 'radio',
        })
    )



