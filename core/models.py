import itertools
import re

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, Sum
from django.utils.functional import cached_property

from core.color_schemes import get_color_for_value
from core.utils.n50 import calculate_n50


class MLVAUser(models.Model):
    """
    Extended user model for MLVA application.
    Stores application-specific user metadata.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='mlva_profile',
        primary_key=True
    )
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    settings = models.JSONField(
        default=dict,
        help_text="User preferences and settings stored as JSON"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "MLVA Users"

    def __str__(self):
        return f"{self.user.username} ({self.first_name} {self.last_name})"
    
    def get_color_scheme(self):
        """Get the user's preferred color scheme, default to 'default'"""
        return self.settings.get('color_scheme', 'default')


class Investigation(models.Model):
    """
    A research investigation containing related isolates and assemblies.
    Serves as the top-level container for organizing related samples.
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
        ('archived', 'Archived'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Investigations"
        unique_together = ('name', 'created_at')

    def __str__(self):
        return f"{self.name}"

    def get_isolate_count(self):
        return self.isolates.count()

    def get_total_contigs(self):
        total = 0
        for isolate in self.isolates.all():
            total += isolate.contigs.count()
        return total


class UserInvestigationQuerySet(models.QuerySet):
    
    def annotate_counts(self):
        return self.annotate(
            analyses_count=Count('investigation__mlva_configs', distinct=True),
            isolates_count=Count('investigation__isolates', distinct=True)
        )

    def apply_filters(self, filters):
        queryset = self
        if filters.get('search_name'):
            queryset = queryset.filter(investigation__name__icontains=filters['search_name'])
        if filters.get('status'):
            queryset = queryset.filter(investigation__status=filters['status'])
        if filters.get('date_from'):
            queryset = queryset.filter(investigation__created_at__date__gte=filters['date_from'])
        if filters.get('date_to'):
            queryset = queryset.filter(investigation__created_at__date__lte=filters['date_to'])
        return queryset

    def apply_sorting(self, sort_by, sort_order):
        # Map incoming sort strings to actual ORM lookup paths
        SORT_MAP = {
            'name': 'investigation__name',
            'status': 'investigation__status',
            'created_at': 'investigation__created_at',
            'role': 'role',  # Assumed role is directly on UserInvestigation
            'analyses': 'analyses_count',
        }
        
        # Fallback to default if an invalid sort is passed
        sort_field = SORT_MAP.get(sort_by, 'investigation__created_at')
        
        # Apply descending prefix if necessary
        if sort_order == 'desc':
            sort_field = f'-{sort_field}'
            
        return self.order_by(sort_field)


class UserInvestigation(models.Model):
    """
    Junction table linking users to investigations with specific roles/permissions.
    Enables sharing investigations between researchers with granular access control.
    """
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('editor', 'Editor'),
        ('viewer', 'Viewer'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='investigation_access'
    )
    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.CASCADE,
        related_name='user_access'
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='viewer'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserInvestigationQuerySet.as_manager()

    class Meta:
        unique_together = ('user', 'investigation')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.investigation.name} ({self.role})"

    def can_edit(self):
        """Check if user has edit permissions."""
        return self.role in ['owner', 'editor']

    def can_view(self):
        """Check if user has view permissions."""
        return self.role in ['owner', 'editor', 'viewer']


class Isolate(models.Model):
    """
    A bacterial isolate/strain associated with an investigation.
    Contains metadata about the sample and references its contigs.
    """

    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.CASCADE,
        related_name="isolates",
    )

    name = models.CharField(max_length=100)
    sampling_date = models.DateField()
    sampling_location = models.CharField(max_length=255)
    notes = models.TextField(blank=True, null=True)

    assembly_size = models.BigIntegerField(default=0)
    contig_count = models.PositiveIntegerField(default=0)

    active = models.BooleanField(
        default=True,
        help_text="Whether this isolate is included in analyses and visualizations",
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    n50 = models.IntegerField(
        blank=True,
        null=True,
        default=0,
        help_text="N50 value calculated from contig lengths",
    )

    class Meta:
        ordering = ["-sampling_date"]
        verbose_name_plural = "Isolates"

        constraints = [
            models.UniqueConstraint(
                fields=["investigation", "name"],
                name="unique_isolate_name_per_investigation",
            )
        ]

        indexes = [
            models.Index(fields=["investigation", "active"]),
            models.Index(fields=["sampling_date"]),
        ]

    def __str__(self):
        return self.name

    def __lt__(self, other):
        return self.n50 < other.n50

    def __gt__(self, other):
        return self.n50 > other.n50


    def calculate_and_update_n50(self):
        """
        Calculate the N50 value from all contigs of this isolate
        and update the stored field.
        """
        contig_lengths = self.contigs.values_list(
            "length",
            flat=True,
        )

        self.n50 = calculate_n50(contig_lengths)
        self.save(update_fields=["n50"])

    def calculate_and_update_stats(self):
        """
        Calculate and update assembly size, contig count, and N50 values
        from all contigs of this isolate. This is more efficient than
        calling individual calculation methods when multiple stats need updating.
        """
        contig_data = self.contigs.aggregate(
            total_size=Sum("length"),
            total_count=Count("id")
        )

        self.assembly_size = contig_data["total_size"] or 0
        self.contig_count = contig_data["total_count"] or 0

        contig_lengths = self.contigs.values_list("length", flat=True)
        self.n50 = calculate_n50(contig_lengths)

        self.save(update_fields=["assembly_size", "contig_count", "n50"])


class Contig(models.Model):
    """
    A contig (contiguous sequence) from a genome assembly.
    Contains the FASTA header, the actual DNA sequence and associated metrics.
    """
    isolate = models.ForeignKey(
        Isolate,
        on_delete=models.CASCADE,
        related_name='contigs'
    )
    fasta_header = models.CharField(max_length=500)
    sequence = models.TextField()
    length = models.IntegerField(validators=[MinValueValidator(1)])
    gc_content = models.FloatField(
        validators=[MinValueValidator(0), MinValueValidator(100)],
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['isolate', 'fasta_header']

    def __str__(self):
        return f"{self.fasta_header} ({self.length} bp)"

    def calculate_gc_content(self) -> float:
        """Calculate GC content percentage from sequence."""
        if not self.sequence:
            return 0.0
        gc_count = self.sequence.upper().count('G') + self.sequence.upper().count('C')
        return (gc_count / len(self.sequence)) * 100 if self.sequence else 0.0

    def save(self, *args, **kwargs):
        """Auto-calculate length and GC content before saving."""
        if self.sequence:
            self.length = len(self.sequence)
            self.gc_content = self.calculate_gc_content()
        super().save(*args, **kwargs)


class MLVAConfig(models.Model):
    """
    Configuration for running MLVA analysis on an investigation.
    Stores configurable parameters like locus set and counting mode.
    """
    COUNT_MODE_CHOICES = [
        ('uncounted', 'Uncounted'),
        ('counted', 'Counted'),
    ]

    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.CASCADE,
        related_name='mlva_configs'
    )
    locus_set = models.ForeignKey(
        'LocusSet',
        on_delete=models.CASCADE
    )
    count_mode = models.CharField(
        max_length=20,
        choices=COUNT_MODE_CHOICES,
        default='uncounted',
        help_text="How to count repeats in tandem repeat regions"
    )
    jobs = models.JSONField(
        default=dict,
        help_text="Dictionary mapping job IDs to job metadata (status, timestamps, etc)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "MLVA Configuration"
        verbose_name_plural = "MLVA Configurations"
        unique_together = ('investigation', 'locus_set', 'count_mode')

    def __str__(self):
        return f"{self.investigation.name} - {self.locus_set} ({self.count_mode})"

    def profiles(self):
        """
        Returns a nested dictionary of results: {isolate_id: {locus_name: count}}
        Filtered by the LocusSet linked to this config.
        """
        # Get names of loci actually belonging to the linked locus_set
        locus_names = self.locus_set.loci.values_list('name', flat=True)
        
        results = (
            LocusResult.objects
            .filter(
                mlva_config=self,
                locus__locus_set=self.locus_set  # Ensure results match current set
            )
            .values('contig__isolate_id', 'locus__name', 'repeat_count')
        )

        # Build a nested lookup: {isolate_id: {locus_name: repeat_count}}
        profiles = {}
        for r in results:
            isolate_id = r['contig__isolate_id']
            locus_name = r['locus__name']
            
            if isolate_id not in profiles:
                profiles[isolate_id] = {}
            profiles[isolate_id][locus_name] = r['repeat_count']
            
        return profiles

    def calculate_total_repeats_for_isolate(self, isolate_id):
        """
        Calculate the total sum of repeat counts for an isolate across all loci.
        
        Args:
            isolate_id: The ID of the isolate
            
        Returns:
            int: Total sum of repeat counts, or None if no results found
        """
        results = (
            LocusResult.objects
            .filter(
                mlva_config=self,
                contig__isolate_id=isolate_id,
                locus__locus_set=self.locus_set
            )
            .values_list('repeat_count', flat=True)
            .distinct()  # Avoid double-counting if isolate has multiple contigs
        )
        
        total = 0
        for repeat_count_str in results:
            value, _ = self._parse_repeat_count(repeat_count_str)
            if value is not None:
                total += value
        
        return total if total > 0 else None

    def profiles_table(self, color_scheme='default'):
        """
        Formats the profiles into a table-friendly structure with color coding.
        Numeric values are colored on a gradient based on their value relative to min/max.
        Separates active and inactive isolates.
        
        Args:
            color_scheme: Name of the color scheme to use (default: 'default')
        """
        # Get the ordered list of locus names from the set
        loci = list(self.locus_set.loci.values_list('name', flat=True))
        all_profiles = self.profiles()
        
        # First pass: collect all numeric values from ACTIVE isolates to calculate min/max
        all_values = []
        rows_raw = []
        for isolate in self.investigation.isolates.all():
            isolate_data = all_profiles.get(isolate.id, {})
            raw_values = [isolate_data.get(name) for name in loci]
            rows_raw.append((isolate, raw_values))
            
            # Collect numeric values for min/max calculation (only from active isolates)
            if isolate.active:
                for val in raw_values:
                    if val is not None:
                        # Try to convert string to int
                        try:
                            num_val = int(val) if isinstance(val, str) else val
                            if isinstance(num_val, (int, float)):
                                all_values.append(num_val)
                        except (ValueError, TypeError):
                            # If direct conversion fails, try to extract numeric value from strings like ">=5"
                            if isinstance(val, str):
                                numeric_match = re.search(r'(\d+)', val)
                                if numeric_match:
                                    try:
                                        extracted_num = int(numeric_match.group(1))
                                        all_values.append(extracted_num)
                                    except (ValueError, AttributeError):
                                        pass
        
        # Calculate min/max for coloring (based on active isolates only)
        min_value = min(all_values) if all_values else 0
        max_value = max(all_values) if all_values else 0
        
        # Calculate pairwise STRD distances for active isolates
        pairwise_data = self.calculate_pairwise_distances()
        active_isolate_ids = set(isolate.id for isolate in self.investigation.isolates.filter(active=True))
        all_isolate_ids = set(pairwise_data['isolates'])
        
        # Build closest STRD map: {isolate_id: {'min_strd': value, 'closest_isolates': [names]}}
        # For all isolates: find closest isolate (active or inactive)
        closest_strd_map = {}
        for isolate_id in all_isolate_ids:
            min_strd = float('inf')
            closest_isolate_ids = set()
            
            for (iso_1_id, iso_2_id), distance_data in pairwise_data['distances'].items():
                # Check if this pair involves our isolate
                if iso_1_id == isolate_id:
                    other_id = iso_2_id
                elif iso_2_id == isolate_id:
                    other_id = iso_1_id
                else:
                    continue
                
                # Consider distances to all isolates (active or inactive)
                strd_value = distance_data['strd']
                if strd_value < min_strd:
                    min_strd = strd_value
                    closest_isolate_ids = {other_id}
                elif strd_value == min_strd:
                    closest_isolate_ids.add(other_id)
            
            if min_strd != float('inf'):
                closest_strd_map[isolate_id] = {
                    'min_strd': min_strd,
                    'closest_isolates': [pairwise_data['isolate_names'][iso_id] for iso_id in sorted(closest_isolate_ids)]
                }
            else:
                closest_strd_map[isolate_id] = {
                    'min_strd': None,
                    'closest_isolates': []
                }
        
        # Helper function to color values
        def color_value(val, is_active):
            if val is None:
                # No data
                return {
                    'value': None,
                    'bg_color': None,
                    'text_class': 'text-base-content/40'
                }
            
            # Try to convert to numeric value
            numeric_val = None
            try:
                numeric_val = int(val) if isinstance(val, str) else val
                if not isinstance(numeric_val, (int, float)):
                    numeric_val = None
            except (ValueError, TypeError):
                numeric_val = None
            
            if numeric_val is not None:
                if is_active:
                    # Use color scheme helper for active isolates
                    bg_color = get_color_for_value(numeric_val, min_value, max_value, color_scheme)
                else:
                    # Use grey for inactive isolates
                    bg_color = '#d1d5db'  # grey-300
                
                return {
                    'value': val,
                    'bg_color': bg_color,
                    'text_class': 'text-base-content'
                }
            else:
                # String or other type - try to extract numeric values
                bg_color = None
                
                if is_active:
                    # Try to extract numeric value from strings like ">=5"
                    numeric_match = re.search(r'(\d+)', val)
                    if numeric_match:
                        try:
                            extracted_num = int(numeric_match.group(1))
                            # Use color scheme helper for extracted number
                            bg_color = get_color_for_value(extracted_num, min_value, max_value, color_scheme)
                        except (ValueError, AttributeError):
                            pass
                else:
                    # For inactive: grey background for any non-null value
                    bg_color = '#d1d5db'  # grey-300
                
                return {
                    'value': val,
                    'bg_color': bg_color,
                    'text_class': 'text-base-content/70',
                    'is_string': bg_color is None  # Only mark as string if no color was applied
                }
        
        # Second pass: build rows with color information, separating active and inactive
        active_rows = []
        inactive_rows = []
        for isolate, raw_values in rows_raw:
            colored_values = [color_value(val, isolate.active) for val in raw_values]
            
            row_data = {
                "isolate": isolate.name,
                "isolate_id": isolate.id,
                "metadata": {
                    "sampling_date": isolate.sampling_date.isoformat() if isolate.sampling_date else None,
                    "sampling_location": isolate.sampling_location,
                    "notes": isolate.notes,
                    "assembly_size": isolate.assembly_size,
                    "contig_count": isolate.contig_count,
                    "n50": isolate.n50,
                    "closest_strd": closest_strd_map.get(isolate.id, {'min_strd': None, 'closest_isolates': []}),
                    "total_repeats": self.calculate_total_repeats_for_isolate(isolate.id),
                },
                "values": colored_values,
                "active": isolate.active,
            }
            
            if isolate.active:
                active_rows.append(row_data)
            else:
                inactive_rows.append(row_data)

        return {
            "headers": loci,
            "active_rows": active_rows,
            "inactive_rows": inactive_rows,
            "min_value": min_value,
            "max_value": max_value,
        }

    def completion(self):
        """Check if all jobs in this MLVA configuration have completed.
        
        Returns a string displaying the percentage of jobs that were completed.
        """
        if self.jobs:
            total = len(self.jobs.values())
            completed = sum(1 for job in self.jobs.values() if job.get('status') == 'completed')
            percentage = completed / total * 100
            return f"{percentage}%"
        else:
            return "Not started"

    def _parse_repeat_count(self, repeat_count_str):
        """Parse a repeat count value, handling approximate prefixes like '>', '>=', etc.
        
        Args:
            repeat_count_str: String like "5", ">5", ">=15", or "DC"
            
        Returns:
            Tuple of (value, is_approximate) where:
            - value: int repeat count (or None if cannot parse)
            - is_approximate: bool True if value had any prefix like '>', '>=', '~', etc.
        """
        import re
        
        if not repeat_count_str or repeat_count_str == "DC":
            return None, False
        
        repeat_count_str = str(repeat_count_str).strip()
        is_approximate = False
        
        # Check if there's any non-numeric prefix (>, >=, <, <=, ~, etc.)
        match = re.match(r'^([^0-9]*)(\d+)$', repeat_count_str)
        
        if match:
            prefix, numeric_part = match.groups()
            if prefix:  # There was a prefix
                is_approximate = True
            try:
                value = int(numeric_part)
                return value, is_approximate
            except ValueError:
                return None, False
        
        # If no match, try parsing as plain number
        try:
            value = int(repeat_count_str)
            return value, False
        except (ValueError, TypeError):
            return None, False

    def calculate_pairwise_distances(self):
        """Calculate pairwise distances for all isolates in this MLVA config.
        
        Computes the absolute distance between each pair of isolates at each locus.
        Distances marked with '*' indicate at least one value was approximated ('>').
        
        Returns:
            Dictionary with structure:
            {
                'isolates': [list of isolate ids in order],
                'isolate_names': {isolate_id: name},
                'loci': [list of locus names],
                'distances': {
                    (iso_id_1, iso_id_2): {
                        'strd': total_distance,
                        'marked': bool (True if any '>' values used),
                        'per_locus': {locus_name: distance}
                    }
                }
            }
        """        
        # Get all isolates and loci in order
        isolates = self.investigation.isolates.all().order_by('name')
        loci_list = self.locus_set.loci.all().order_by('name')
        
        isolate_ids = list(isolates.values_list('id', flat=True))
        isolate_names = {iso.id: iso.name for iso in isolates}
        locus_names = list(loci_list.values_list('name', flat=True))
        
        # Get all LocusResults for this config, indexed by (isolate_id, locus_id)
        # Filter to only include loci from this config's locus_set
        # For isolates with multiple contigs, we take the first (or any) result per isolate-locus pair
        results = (
            LocusResult.objects
            .filter(mlva_config=self, locus__locus_set=self.locus_set)
            .select_related('contig__isolate', 'locus')
            .order_by('contig__isolate_id', 'locus_id', 'contig_id')  # Order to get consistent results
            .values('contig__isolate_id', 'locus_id', 'locus__name', 'repeat_count')
        )
        
        # Build lookup: {(isolate_id, locus_id): repeat_count}
        # If an isolate has multiple contigs, keep the first result per isolate-locus pair
        result_lookup = {}
        for r in results:
            key = (r['contig__isolate_id'], r['locus_id'])
            if key not in result_lookup:  # Only set if not already present
                result_lookup[key] = r['repeat_count']
        
        # Calculate pairwise distances
        distances = {}
        for iso_id_1, iso_id_2 in itertools.combinations(isolate_ids, 2):
            strd = 0
            marked = False
            per_locus = {}
            
            for locus in loci_list:
                # Get repeat counts for both isolates at this locus
                val_1_str = result_lookup.get((iso_id_1, locus.id))
                val_2_str = result_lookup.get((iso_id_2, locus.id))
                
                # Parse values
                val_1, approx_1 = self._parse_repeat_count(val_1_str)
                val_2, approx_2 = self._parse_repeat_count(val_2_str)
                
                # If either is None, skip this locus
                if val_1 is None or val_2 is None:
                    continue
                
                # Track if this distance uses approximate values
                if approx_1 or approx_2:
                    marked = True
                
                # Calculate distance
                distance = abs(val_1 - val_2)
                strd += distance
                per_locus[locus.name] = distance
            
            # Store with consistent ordering: smaller ID first
            pair_key = tuple(sorted([iso_id_1, iso_id_2]))
            distances[pair_key] = {
                'strd': strd,
                'marked': marked,
                'per_locus': per_locus,
            }
        
        return {
            'isolates': isolate_ids,
            'isolate_names': isolate_names,
            'loci': locus_names,
            'distances': distances,
        }

class Locus(models.Model):
    """
    A genetic locus used in MLVA analysis.
    Defines the forward primer, reverse primer, and repeat sequence for a specific region.
    """
    locus_set = models.ForeignKey(
        'LocusSet',
        on_delete=models.CASCADE,
        related_name='loci'
    )
    name = models.CharField(max_length=100)
    forward_primer = models.CharField(max_length=500, help_text="5' to 3' forward primer sequence")
    reverse_primer = models.CharField(max_length=500, help_text="5' to 3' reverse primer sequence")
    repeat_sequence = models.CharField(
        max_length=500,
        help_text="Tandem repeat sequence to count"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = "Loci"
        unique_together = ('locus_set', 'name')

    @property
    def repeat_length(self):
        """Return the count of repeat units: each bracketed section counts as 1, 
        plus all nucleotides outside brackets count as 1 each."""
        return self.repeat_sequence.count('[') + len(re.sub(r'\[.*?\]', '', self.repeat_sequence))


class LocusSetQuerySet(models.QuerySet):
    def annotate_counts(self):
        """Annotate each LocusSet with the count of Loci it contains."""
        return self.annotate(
            loci_count=Count('loci', distinct=True)
        )

    def apply_filters(self, filters):
        """Apply filters to LocusSet queryset.
        
        Supports filtering by:
        - search_name: Filter by name or description (case-insensitive)
        - search_locus_name: Filter by locus name (case-insensitive)
        - filter_loci_count: Filter by exact number of loci
        """
        queryset = self
        if filters.get('search_name'):
            queryset = queryset.filter(
                models.Q(name__icontains=filters['search_name']) |
                models.Q(description__icontains=filters['search_name'])
            )
        if filters.get('search_locus_name'):
            queryset = queryset.filter(
                loci__name__icontains=filters['search_locus_name']
            ).distinct()
        if filters.get('filter_loci_count'):
            try:
                count = int(filters['filter_loci_count'])
                queryset = queryset.filter(loci_count=count)
            except (ValueError, TypeError):
                pass  # Invalid count value, skip this filter
        return queryset

    def apply_sorting(self, sort_by, sort_order):
        """Apply sorting to LocusSet queryset.
        
        Supports sorting by: name, loci_count
        """
        # Map incoming sort strings to actual ORM lookup paths
        SORT_MAP = {
            'name': 'name',
            'loci_count': 'loci_count',
        }
        
        # Fallback to default (name) if an invalid sort is passed
        sort_field = SORT_MAP.get(sort_by, 'name')
        
        # Apply descending prefix if necessary
        if sort_order == 'desc':
            sort_field = f'-{sort_field}'
            
        return self.order_by(sort_field)



class LocusSet(models.Model):
    """
    A set of Locus objects that can be used in an MLVAConfig.
    """
    name = models.CharField(max_length=20)
    description = models.CharField(max_length=200)

    objects = LocusSetQuerySet.as_manager()

    def __repr__(self):
        return f"{self.name}"

    def __str__(self):
        return f"{self.name}"
    
    @property
    def loci_list(self):
        """
        Return a sorted list of locus names in this set.
        """
        return sorted(self.loci.values_list('name', flat=True))

class LocusResult(models.Model):
    """
    Result of MLVA analysis for a specific isolate at a specific locus.
    Stores the repeat count for the isolate-locus combination.
    """
    mlva_config = models.ForeignKey(
        MLVAConfig,
        on_delete=models.CASCADE,
        related_name='locus_results'
    )
    contig = models.ForeignKey(
        Contig,
        on_delete=models.CASCADE,
        related_name='locus_results'
    )
    locus = models.ForeignKey(
        Locus,
        on_delete=models.CASCADE,
        related_name='results'
    )
    repeat_count = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['contig', 'locus']
        verbose_name_plural = "Locus Results"
        unique_together = ('mlva_config', 'contig', 'locus')
        indexes = [
            models.Index(fields=['mlva_config', 'contig']),
            models.Index(fields=['contig', 'locus']),
        ]

    def __str__(self):
        return f"{self.contig.isolate.name} @ {self.locus.name}: {self.repeat_count} repeats"
