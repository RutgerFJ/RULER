from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count
from .models import (
    MLVAUser, Investigation, UserInvestigation,
    Isolate, Contig,
    MLVAConfig, Locus, LocusSet, LocusResult
)


@admin.register(MLVAUser)
class MLVAUserAdmin(admin.ModelAdmin):
    """Admin interface for MLVAUser model."""
    list_display = ('user', 'first_name', 'last_name', 'created_at')
    list_filter = ('created_at', 'updated_at')
    search_fields = ('user__username', 'user__email', 'first_name', 'last_name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('User Account', {
            'fields': ('user',)
        }),
        ('Profile Information', {
            'fields': ('first_name', 'last_name')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        """Prevent manual creation; MLVAUser is created via signup."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of user profiles."""
        return False


@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    """Admin interface for Investigation model."""
    list_display = ('id', 'name', 'status', 'isolate_count', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'status')
        }),
        ('Details', {
            'fields': ('description',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description='Isolates', ordering='isolate_count')
    def isolate_count(self, obj):
        count = obj.isolates.count()
        return format_html(
            '<span style="background-color: #e8f4f8; padding: 2px 6px; border-radius: 3px;">{}</span>',
            count
        )

    def get_queryset(self, request):
        """Optimize queryset with counts."""
        qs = super().get_queryset(request)
        return qs.annotate(isolate_count=Count('isolates'))


class IsolateInline(admin.TabularInline):
    """Inline admin for Isolate within Investigation."""
    model = Isolate
    extra = 1
    fields = ('name', 'sampling_date', 'sampling_location')
    readonly_fields = ('created_at',)


@admin.register(Isolate)
class IsolateAdmin(admin.ModelAdmin):
    """Admin interface for Isolate model."""
    list_display = ('name', 'investigation', 'sampling_date', 'sampling_location', 'contig_count')
    list_filter = ('investigation', 'sampling_date')
    search_fields = ('name', 'investigation__name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Parent Investigation', {
            'fields': ('investigation',)
        }),
        ('Sample Metadata', {
            'fields': ('name', 'sampling_date', 'sampling_location', 'notes')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description='Contigs')
    def contig_count(self, obj):
        count = obj.contigs.count()
        return format_html(
            '<span style="background-color: #fff3cd; padding: 2px 6px; border-radius: 3px;">{}</span>',
            count
        )


class ContigInline(admin.TabularInline):
    """Inline admin for Contig within Isolate."""
    model = Contig
    extra = 0
    fields = ('fasta_header', 'length', 'gc_content')
    readonly_fields = ('length', 'gc_content', 'created_at')
    can_delete = False

@admin.register(Contig)
class ContigAdmin(admin.ModelAdmin):
    """Admin interface for Contig model."""
    list_display = ('fasta_header', 'isolate', 'length_formatted', 'gc_content_formatted')
    list_filter = ('isolate__investigation', 'created_at')
    search_fields = ('fasta_header', 'isolate__name')
    readonly_fields = ('length', 'gc_content', 'created_at', 'sequence_preview')
    fieldsets = (
        ('Contig Info', {
            'fields': ('isolate', 'fasta_header')
        }),
        ('Sequence Data', {
            'fields': ('sequence', 'sequence_preview')
        }),
        ('Metrics', {
            'fields': ('length', 'gc_content')
        }),
        ('Timestamp', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        """Optimize queryset."""
        qs = super().get_queryset(request)
        return qs.select_related('isolate__investigation')

    @admin.display(description='Length')
    def length_formatted(self, obj):
        return format_html('{:,} bp', obj.length)

    @admin.display(description='GC Content')
    def gc_content_formatted(self, obj):
        if obj.gc_content is None:
            return '-'
        color = '#d4edda' if 40 <= obj.gc_content <= 60 else '#f8d7da'
        return format_html(
            '<span style="background-color: {}; padding: 2px 6px; border-radius: 3px;">{:.1f}%</span>',
            color,
            obj.gc_content
        )

    @admin.display(description='Sequence Preview')
    def sequence_preview(self, obj):
        if not obj.sequence:
            return '-'
        preview = obj.sequence[:100]
        if len(obj.sequence) > 100:
            preview += '...'
        return format_html(
            '<code style="background-color: #f5f5f5; padding: 5px; border-radius: 3px; display: block; overflow-x: auto;">{}</code>',
            preview
        )


@admin.register(UserInvestigation)
class UserInvestigationAdmin(admin.ModelAdmin):
    """Admin interface for UserInvestigation model."""
    list_display = ('user', 'investigation', 'role', 'created_at')
    list_filter = ('role', 'created_at', 'investigation')
    search_fields = ('user__username', 'investigation__name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Access Control', {
            'fields': ('user', 'investigation', 'role')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_delete_permission(self, request, obj=None):
        """Allow deletion only if user is admin."""
        return request.user.is_staff and request.user.is_superuser


@admin.register(LocusSet)
class LocusSetAdmin(admin.ModelAdmin):
    """Admin interface for LocusSet model."""
    list_display = ('name', 'description', 'locus_count')
    search_fields = ('name', 'description')
    
    @admin.display(description='Loci Count')
    def locus_count(self, obj):
        count = obj.loci.count()
        return format_html(
            '<span style="background-color: #d1ecf1; padding: 2px 6px; border-radius: 3px;">{}</span>',
            count
        )


class LocusInline(admin.TabularInline):
    """Inline admin for Locus within LocusSet."""
    model = Locus
    extra = 1
    fields = ('name', 'forward_primer', 'reverse_primer', 'repeat_sequence')


@admin.register(Locus)
class LocusAdmin(admin.ModelAdmin):
    """Admin interface for Locus model."""
    list_display = ('name', 'locus_set', 'forward_primer_preview', 'reverse_primer_preview')
    list_filter = ('locus_set', 'created_at')
    search_fields = ('name', 'locus_set__name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Basic Info', {
            'fields': ('locus_set', 'name')
        }),
        ('Primer Sequences', {
            'fields': ('forward_primer', 'reverse_primer')
        }),
        ('Repeat Sequence', {
            'fields': ('repeat_sequence',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description='Forward Primer')
    def forward_primer_preview(self, obj):
        preview = obj.forward_primer[:50]
        if len(obj.forward_primer) > 50:
            preview += '...'
        return format_html(
            '<code style="background-color: #f5f5f5; padding: 2px 4px; border-radius: 3px;">{}</code>',
            preview
        )

    @admin.display(description='Reverse Primer')
    def reverse_primer_preview(self, obj):
        preview = obj.reverse_primer[:50]
        if len(obj.reverse_primer) > 50:
            preview += '...'
        return format_html(
            '<code style="background-color: #f5f5f5; padding: 2px 4px; border-radius: 3px;">{}</code>',
            preview
        )


@admin.register(MLVAConfig)
class MLVAConfigAdmin(admin.ModelAdmin):
    """Admin interface for MLVAConfig model."""
    list_display = ('investigation', 'locus_set', 'count_mode', 'result_count', 'created_at')
    list_filter = ('count_mode', 'locus_set', 'created_at')
    search_fields = ('investigation__name', 'locus_set__name')
    readonly_fields = ('created_at', 'updated_at', 'jobs_display')
    fieldsets = (
        ('Configuration', {
            'fields': ('investigation', 'locus_set', 'count_mode')
        }),
        ('Jobs', {
            'fields': ('jobs', 'jobs_display'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        """Optimize queryset."""
        qs = super().get_queryset(request)
        return qs.select_related('investigation', 'locus_set')

    @admin.display(description='Results')
    def result_count(self, obj):
        count = obj.locus_results.count()
        return format_html(
            '<span style="background-color: #c3e6cb; padding: 2px 6px; border-radius: 3px;">{}</span>',
            count
        )

    @admin.display(description='Jobs Data')
    def jobs_display(self, obj):
        if not obj.jobs:
            return '-'
        import json
        jobs_json = json.dumps(obj.jobs, indent=2)
        return format_html(
            '<pre style="background-color: #f5f5f5; padding: 10px; border-radius: 3px; overflow-x: auto;">{}</pre>',
            jobs_json
        )


@admin.register(LocusResult)
class LocusResultAdmin(admin.ModelAdmin):
    """Admin interface for LocusResult model."""
    list_display = ('contig_isolate_name', 'locus_name', 'repeat_count', 'mlva_config', 'created_at')
    list_filter = ('mlva_config__investigation', 'mlva_config__locus_set', 'created_at')
    search_fields = ('contig__isolate__name', 'contig__fasta_header', 'locus__name')
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Configuration', {
            'fields': ('mlva_config',)
        }),
        ('Result Data', {
            'fields': ('contig', 'locus', 'repeat_count')
        }),
        ('Timestamp', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        """Optimize queryset."""
        qs = super().get_queryset(request)
        return qs.select_related(
            'mlva_config__investigation',
            'mlva_config__locus_set',
            'contig__isolate',
            'locus__locus_set'
        )

    @admin.display(description='Isolate')
    def contig_isolate_name(self, obj):
        return obj.contig.isolate.name

    @admin.display(description='Locus')
    def locus_name(self, obj):
        return obj.locus.name
