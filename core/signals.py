"""
Django signals for automatic N50 recalculation.

Whenever a Contig is created, updated, or deleted, the parent Isolate's N50
value is automatically recalculated and updated.
"""

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from core.models import Contig


@receiver(post_save, sender=Contig)
def recalculate_isolate_n50_on_contig_save(sender, instance, created, **kwargs):
    """
    Recalculate N50, assembly size, and contig count when a contig is created or updated.
    
    This signal handler is triggered whenever a Contig is saved (created or modified).
    It recalculates the parent Isolate's stats to reflect the updated contigs.
    """
    if instance.isolate:
        instance.isolate.calculate_and_update_stats()


@receiver(post_delete, sender=Contig)
def recalculate_isolate_n50_on_contig_delete(sender, instance, **kwargs):
    """
    Recalculate N50, assembly size, and contig count when a contig is deleted.
    
    This signal handler is triggered whenever a Contig is deleted.
    It recalculates the parent Isolate's stats to reflect the remaining contigs.
    """
    if instance.isolate:
        instance.isolate.calculate_and_update_stats()
