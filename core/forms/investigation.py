import csv
import zipfile
from io import StringIO
from datetime import datetime
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError

from core.models import Investigation


# ============================================================================
# Investigation Creation Multi-Step Forms
# ============================================================================

class BasicInvestigationForm(forms.Form):
    """
    Step 1: Collect basic investigation information.
    Validates name uniqueness and description length.
    """
    name = forms.CharField(
        max_length=255,
        required=True,
        label="Investigation Name",
        widget=forms.TextInput(attrs={
            'class': 'input input-bordered w-full bg-base-100',
            'placeholder': 'e.g., Outbreak Investigation 2024-01',
            'autofocus': True
        })
    )
    description = forms.CharField(
        max_length=2000,
        required=False,
        label="Description",
        widget=forms.Textarea(attrs={
            'class': 'textarea textarea-bordered w-full bg-base-100 pt-3 leading-normal',
            'placeholder': 'Optional: Enter investigation context, goals, or notes...',
            'rows': 4,
        })
    )

    def clean_name(self):
        """Ensure investigation name is not already taken."""
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise ValidationError("Investigation name cannot be empty.")
        
        if Investigation.objects.filter(name=name).exists():
            raise ValidationError(f"An investigation named '{name}' already exists.")
        
        return name

    def clean_description(self):
        """Sanitize description."""
        description = self.cleaned_data.get('description', '').strip()
        return description


class AssemblyUploadForm(forms.Form):
    """
    Step 2: Upload and validate ZIP file containing FASTA assemblies.
    Validates ZIP structure, file types, and FASTA format.
    """
    ALLOWED_EXTENSIONS = {'.fasta', '.fa', '.fna', '.fas'}
    MAX_FASTA_SIZE = 500 * 1024 * 1024  # 500 MB
    MAX_ZIP_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

    assembly_zip = forms.FileField(
        required=True,
        label="Assembly ZIP File",
        help_text="Upload a .zip containing FASTA files (*.fasta, *.fa, *.fna)",
        widget=forms.FileInput(attrs={
            'class': 'file-input file-input-primary bg-base-100',
            'accept': '.zip',
        })
    )

    def clean_assembly_zip(self):
        """
        Validate ZIP file structure and contents.
        Returns dict of {isolate_name: [(header, sequence), ...]}.
        """
        zip_file = self.cleaned_data.get('assembly_zip')
        if not zip_file:
            raise ValidationError("ZIP file is required.")

        # Check file size
        if zip_file.size > self.MAX_ZIP_SIZE:
            raise ValidationError(
                f"ZIP file is too large ({zip_file.size / 1024 / 1024:.1f} MB). "
                f"Maximum size is {self.MAX_ZIP_SIZE / 1024 / 1024 / 1024:.1f} GB."
            )

        # Validate ZIP structure
        try:
            with zipfile.ZipFile(zip_file, 'r') as zf:
                file_list = zf.namelist()
                
                # Filter out macOS system files/directories first
                # These are commonly added by macOS and should be ignored
                system_prefixes = (
                    '__MACOSX/', '__MACOSX',
                    '.DS_Store',
                    '.AppleDouble/', '.AppleDouble',
                    '.TemporaryItems/', '.TemporaryItems',
                    '._',  # Resource fork files
                )
                cleaned_list = [
                    name for name in file_list
                    if not any(name.startswith(prefix) for prefix in system_prefixes)
                ]
                
                # Handle single root directory (common with macOS Finder ZIP)
                # Create mapping of display names to actual ZIP paths
                zip_path_map = {}  # {display_name: actual_zip_path}
                
                root_dirs = set()
                for name in cleaned_list:
                    if '/' in name:
                        root_dirs.add(name.split('/')[0])
                
                # If all files are in exactly one directory, use it
                if len(root_dirs) == 1:
                    root_dir = list(root_dirs)[0] + '/'
                    for name in cleaned_list:
                        if name.startswith(root_dir):
                            stripped = name[len(root_dir):]
                            # Only keep actual files (not dirs or empty)
                            if stripped and not stripped.endswith('/') and stripped != '.':
                                zip_path_map[stripped] = name
                    cleaned_list = list(zip_path_map.keys())
                elif len(root_dirs) > 1:
                    # Multiple top-level directories - not allowed
                    raise ValidationError(
                        f"ZIP file contains multiple root directories. "
                        f"All FASTA files must be at the root level or in a single folder."
                    )
                else:
                    # No subdirectories, map 1:1
                    for name in cleaned_list:
                        if name and not name.endswith('/') and name != '.':
                            zip_path_map[name] = name
                    cleaned_list = list(zip_path_map.keys())
                
                # Apply system file filtering again after directory processing
                # to ensure nothing slipped through
                cleaned_list = [
                    name for name in cleaned_list
                    if not any(name.startswith(prefix) for prefix in system_prefixes)
                ]
                
                # Filter for FASTA files
                fasta_files = [
                    name for name in cleaned_list
                    if Path(name).suffix.lower() in self.ALLOWED_EXTENSIONS
                ]
                
                if not fasta_files:
                    raise ValidationError(
                        "ZIP file contains no FASTA files. "
                        "Supported formats: .fasta, .fa, .fna, .fas"
                    )
                
                # Identify non-FASTA files and store warning message
                non_fasta_files = [
                    name for name in cleaned_list
                    if name not in fasta_files
                ]
                if non_fasta_files:
                    self.non_fasta_warning = (
                        f"The following non-FASTA files were found and ignored: "
                        f"{', '.join(non_fasta_files[:5])}"
                        f"{'...' if len(non_fasta_files) > 5 else ''}. "
                        f"Only FASTA files will be processed."
                    )
                else:
                    self.non_fasta_warning = None
                
                if len(fasta_files) > 1000:
                    raise ValidationError(
                        f"ZIP contains too many files ({len(fasta_files)}). "
                        "Maximum 1000 assemblies per investigation."
                    )
                
                # Parse and validate FASTA content
                fasta_data = {}
                isolate_names = set()
                
                for fasta_filename in fasta_files:
                    isolate_name = Path(fasta_filename).stem  # Remove extension
                    
                    # Check for duplicate isolate names
                    if isolate_name in isolate_names:
                        raise ValidationError(
                            f"Duplicate isolate name: '{isolate_name}'. "
                            "Each FASTA filename must be unique."
                        )
                    isolate_names.add(isolate_name)
                    
                    # Validate isolate name format
                    if not isolate_name or len(isolate_name) > 255:
                        raise ValidationError(
                            f"Invalid isolate name from file '{fasta_filename}'. "
                            "Names must be 1-255 characters."
                        )
                    
                    # Read and validate FASTA file
                    # Use the actual ZIP path from the mapping
                    actual_zip_path = zip_path_map.get(fasta_filename, fasta_filename)
                    try:
                        fasta_content = zf.read(actual_zip_path).decode('utf-8')
                        sequences = self._parse_fasta(fasta_content, fasta_filename)
                        
                        if not sequences:
                            raise ValidationError(
                                f"File '{fasta_filename}' contains no valid sequences."
                            )
                        
                        fasta_data[isolate_name] = sequences
                        
                    except UnicodeDecodeError:
                        raise ValidationError(
                            f"File '{fasta_filename}' is not valid UTF-8 text."
                        )
                    except Exception as e:
                        raise ValidationError(
                            f"Error parsing FASTA file '{fasta_filename}': {str(e)}"
                        )
                
        except zipfile.BadZipFile:
            raise ValidationError("ZIP file is corrupted or not a valid ZIP file.")
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"Unexpected error processing ZIP file: {str(e)}")

        # Store parsed data for later retrieval
        self.fasta_data = fasta_data
        return zip_file

    @staticmethod
    def _parse_fasta(content, filename):
        """
        Parse FASTA content and return list of (header, sequence) tuples.
        Validates sequence format (ATGC+N only).
        """
        sequences = []
        current_header = None
        current_seq = []
        valid_chars = set('ATGCNatgcn')

        for line in content.split('\n'):
            line = line.rstrip()
            if not line:
                continue
            
            if line.startswith('>'):
                # Save previous sequence
                if current_header is not None:
                    seq = ''.join(current_seq)
                    if seq:
                        sequences.append((current_header, seq))
                    current_seq = []
                
                current_header = line[1:].strip()  # Remove '>' and strip whitespace
                if not current_header:
                    raise ValueError(f"Empty FASTA header in {filename}")
            else:
                # Add to current sequence
                if current_header is None:
                    raise ValueError(f"Sequence without header in {filename}")
                
                # Validate sequence characters
                for char in line:
                    if char not in valid_chars and not char.isspace():
                        raise ValueError(
                            f"Invalid character '{char}' in sequence. "
                            f"Only ATGCN allowed."
                        )
                
                current_seq.append(line.upper())
        
        # Save final sequence
        if current_header is not None:
            seq = ''.join(current_seq)
            if seq:
                sequences.append((current_header, seq))

        return sequences


class MetadataUploadForm(forms.Form):
    """
    Step 3: Upload and validate CSV metadata file.
    Validates structure, required columns, date formats, and isolate name matching.
    """
    REQUIRED_COLUMNS = {'isolate_name', 'sampling_date', 'sampling_location'}
    DATE_FORMATS = ['%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%Y']

    metadata_csv = forms.FileField(
        required=False,  # Make optional - will be validated conditionally
        label="Metadata CSV File",
        help_text="CSV must contain columns: isolate_name, sampling_date (YYYY-MM-DD), sampling_location",
        widget=forms.FileInput(attrs={
            'class': 'file-input file-input-primary bg-base-100',
            'accept': '.csv',
        })
    )

    acknowledge_unmatched = forms.BooleanField(
        required=False,
        label="I acknowledge that metadata entries not matching uploaded assemblies will be ignored",
        widget=forms.CheckboxInput(attrs={
            'class': 'checkbox',
        })
    )

    def clean_metadata_csv(self):
        """
        Validate CSV file structure and contents.
        Returns dict of {isolate_name: {sampling_date, sampling_location, ...}}.
        """
        csv_file = self.cleaned_data.get('metadata_csv')
        if not csv_file:
            raise ValidationError("CSV file is required.")

        # Check file size
        if csv_file.size > 50 * 1024 * 1024:  # 50 MB
            raise ValidationError("CSV file is too large (max 50 MB).")

        metadata = {}
        
        try:
            # Decode and parse CSV
            content = csv_file.read().decode('utf-8')
            reader = csv.DictReader(StringIO(content))
            
            if reader.fieldnames is None:
                raise ValidationError("CSV file is empty or invalid.")
            
            fieldnames_lower = {name.lower(): name for name in reader.fieldnames}
            
            # Check for required columns (case-insensitive)
            missing_columns = []
            for required in self.REQUIRED_COLUMNS:
                if required not in fieldnames_lower:
                    missing_columns.append(required)
            
            if missing_columns:
                raise ValidationError(
                    f"CSV is missing required columns: {', '.join(missing_columns)}. "
                    f"Found columns: {', '.join(reader.fieldnames)}"
                )
            
            # Create mapping from lowercase to actual column names
            isolate_col = fieldnames_lower['isolate_name']
            date_col = fieldnames_lower['sampling_date']
            location_col = fieldnames_lower['sampling_location']
            
            seen_isolates = set()
            row_num = 1
            
            for row_num, row in enumerate(reader, start=2):  # Start at 2 (after header)
                isolate_name = row.get(isolate_col, '').strip()
                sampling_date = row.get(date_col, '').strip()
                sampling_location = row.get(location_col, '').strip()
                
                # Validate isolate name
                if not isolate_name:
                    raise ValidationError(
                        f"Row {row_num}: isolate_name cannot be empty."
                    )
                
                if len(isolate_name) > 255:
                    raise ValidationError(
                        f"Row {row_num}: isolate_name '{isolate_name}' exceeds 255 characters."
                    )
                
                # Check for duplicates
                if isolate_name in seen_isolates:
                    raise ValidationError(
                        f"Row {row_num}: Duplicate isolate_name '{isolate_name}'."
                    )
                seen_isolates.add(isolate_name)
                
                # Validate sampling_date
                if not sampling_date:
                    raise ValidationError(
                        f"Row {row_num}: sampling_date cannot be empty for '{isolate_name}'."
                    )
                
                parsed_date = self._parse_date(sampling_date)
                if not parsed_date:
                    raise ValidationError(
                        f"Row {row_num}: Invalid date format '{sampling_date}' for '{isolate_name}'. "
                        f"Use YYYY-MM-DD format."
                    )
                
                # Validate sampling_location
                if not sampling_location:
                    raise ValidationError(
                        f"Row {row_num}: sampling_location cannot be empty for '{isolate_name}'."
                    )
                
                # Store metadata
                metadata[isolate_name] = {
                    'sampling_date': parsed_date,
                    'sampling_location': sampling_location,
                }
                
                # Store any extra columns
                for key, value in row.items():
                    if key not in [isolate_col, date_col, location_col]:
                        metadata[isolate_name][key] = value.strip() if value else ''
            
            if not metadata:
                raise ValidationError("CSV file contains no data rows.")
        
        except UnicodeDecodeError:
            raise ValidationError("CSV file is not valid UTF-8 text.")
        except csv.Error as e:
            raise ValidationError(f"CSV parsing error: {str(e)}")
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"Unexpected error processing CSV: {str(e)}")

        # Store metadata for later retrieval
        self.metadata = metadata
        return csv_file

    @staticmethod
    def _parse_date(date_str):
        """
        Try to parse date string in multiple formats.
        Returns ISO format string (YYYY-MM-DD) or None if parsing fails.
        """
        for fmt in MetadataUploadForm.DATE_FORMATS:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue
        return None

