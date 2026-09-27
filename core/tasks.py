"""
Background tasks for MLVA analysis using django-rq.

This module contains BLAST and MLVA task functions that are executed by the RQ worker.
"""

from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Dict, List

import django_rq
from rq.job import get_current_job

from django.db import transaction
from django.utils import timezone

from core.utils.calculations import mlva
from core.progress import ProgressTracker
from core.models import Contig, Isolate, LocusSet, MLVAConfig

logger = logging.getLogger(__name__)

CALCULATIONS_DIR = Path(__file__).parent / 'utils' / 'calculations'


def _extract_vntrs_from_locus_set(locus_set: LocusSet) -> Dict[str, str]:
    """
    Extract VNTR repeat sequences from a LocusSet.
    
    Args:
        locus_set: LocusSet instance
    
    Returns:
        Dictionary mapping locus names to repeat sequences
    """
    return {locus.name: locus.repeat_sequence for locus in locus_set.loci.all() if locus.repeat_sequence}


def _build_primers_fasta_from_locus_set(locus_set, output_dir: str) -> str:
    """
    Build a primers FASTA file from LocusSet Locus objects.
    
    Args:
        locus_set: LocusSet instance
        output_dir: Directory to write the FASTA file to
    
    Returns:
        Path to the generated primers FASTA file
    """
    primers_fasta = os.path.join(output_dir, 'generated_primers.fasta')
    
    with open(primers_fasta, 'w') as f:
        for locus in locus_set.loci.all():
            if locus.forward_primer:
                f.write(f">{locus.name}F {locus.repeat_sequence}\n{locus.forward_primer}\n")
            if locus.reverse_primer:
                f.write(f">{locus.name}R {locus.repeat_sequence}\n{locus.reverse_primer}\n")
    
    logger.info(f"Generated primers FASTA file: {primers_fasta}")
    return primers_fasta


def execute_blast_phase(
    assembly_paths: List[str],
    mlva_config: MLVAConfig,
    output_dir: str,
    progress_tracker: ProgressTracker,
    verbose: bool = False
) -> Dict[str, Dict]:
    """Execute BLAST phase using LocusSet from MLVAConfig.
    
    Args:
        assembly_paths: List of paths to assembly FASTA files
        mlva_config: MLVAConfig instance containing LocusSet
        output_dir: Output directory for BLAST results
        progress_tracker: ProgressTracker instance for updates
        verbose: Whether to save verbose BLAST output
        
    Returns:
        Dictionary mapping assembly names to BLAST results
    """
    progress_tracker.set_stage('blast', 'Performing BLAST searches for primers')
    
    # Extract VNTRs from LocusSet
    vntrs = _extract_vntrs_from_locus_set(mlva_config.locus_set)
    logger.info(f"Extracted {len(vntrs)} VNTRs from LocusSet '{mlva_config.locus_set.name}'")
    
    # Build primers FASTA from Locus objects for mlva.find_all_vntrs
    primers_file = _build_primers_fasta_from_locus_set(mlva_config.locus_set, output_dir)
    
    blast_results = {}
    
    for idx, assembly_path in enumerate(assembly_paths):
        assembly_name = Path(assembly_path).stem
        progress_tracker.update_assembly_progress(
            idx,
            len(assembly_paths),
            assembly_name=assembly_name,
            message=f"Running BLAST on {assembly_name}"
        )
        
        try:
            verbose_dir = os.path.join(output_dir, 'blast_verbose') if verbose else None
            if verbose_dir:
                Path(verbose_dir).mkdir(parents=True, exist_ok=True)
            
            # Run BLAST for this assembly
            data, _ = mlva.find_all_vntrs(
                assembly_path,
                primers_file,
                vntrs,
                save_files=verbose_dir
            )
            
            blast_results[assembly_name] = {
                'data': data,
                'assembly_path': assembly_path
            }
            
            logger.info(f"BLAST completed for {assembly_name}")
            
        except Exception as e:
            logger.error(f"Error running BLAST on {assembly_name}: {str(e)}")
            blast_results[assembly_name] = {
                'data': {},
                'lens': {},
                'error': str(e),
                'assembly_path': assembly_path
            }
    
    progress_tracker.update_assembly_progress(
        len(assembly_paths),
        len(assembly_paths),
        message="BLAST phase completed"
    )
    
    return blast_results


def _create_locus_result(mlva_config, contig, locus_name: str, repeat_count: str):
    """
    Create a LocusResult record directly during analysis.
    
    Args:
        mlva_config: MLVAConfig instance
        contig: Contig instance where BLAST hit was found
        locus_name: Name of the locus
        repeat_count: Repeat count value
        
    Returns:
        LocusResult instance
    """
    from core.models import LocusResult, Locus
    
    locus = Locus.objects.get(locus_set=mlva_config.locus_set, name=locus_name)
    result, _ = LocusResult.objects.get_or_create(
        mlva_config=mlva_config,
        contig=contig,
        locus=locus,
        repeat_count=repeat_count
    )
    return result


def execute_mlva_analysis(mlva_config_id: int) -> dict:
    """
    Execute MLVA analysis for an Investigation.
    Updated to process per-Isolate to prevent fragmented BLAST hits
    and incorrect repeat counts.
    """
    from core.models import MLVAConfig, Isolate
    
    job = get_current_job()
    mlva_config = None
    
    try:
        mlva_config = MLVAConfig.objects.get(id=mlva_config_id)
        logger.info(f"Starting MLVA analysis for MLVAConfig {mlva_config.id}")
        
        with tempfile.TemporaryDirectory(prefix='mlva_analysis_') as temp_dir:
            # --- FIX 1: Aggregating Contigs by Isolate ---
            assembly_paths = []
            isolate_map = {} # Map filename stem to Isolate object
            
            # We iterate isolates, not contigs, to create one file per genome
            isolates = mlva_config.investigation.isolates.all()
            
            for isolate in isolates:
                # Create a single FASTA for the WHOLE isolate
                isolate_filename = f"isolate_{isolate.id}"
                isolate_fasta = os.path.join(temp_dir, f"{isolate_filename}.fna")
                
                with open(isolate_fasta, 'w') as f:
                    for contig in isolate.contigs.all():
                        f.write(f">{contig.id}\n") # Use ID as header for easy traceback
                        f.write(f"{contig.sequence}\n")
                
                if os.path.getsize(isolate_fasta) > 0:
                    assembly_paths.append(isolate_fasta)
                    isolate_map[isolate_filename] = isolate
                    logger.info(f"Created consolidated FASTA for isolate {isolate.name}")

            progress_tracker = ProgressTracker(job)
            
            # Phase 1: BLAST (Now running on whole genomes)
            logger.info(f"Phase 1: Running BLAST on {len(assembly_paths)} whole genomes")
            blast_results = execute_blast_phase(
                assembly_paths,
                mlva_config,
                temp_dir,
                progress_tracker,
                verbose=False
            )
            
            # Phase 2: Process results and save best hits
            logger.info("Phase 2: Saving best hits to database")
            progress_tracker.set_stage('mlva', 'Finalizing MLVA records')
            
            mlva_profile_count = 0
            
            for file_stem, blast_data in blast_results.items():
                isolate = isolate_map.get(file_stem)
                if not isolate or 'error' in blast_data:
                    continue
                
                profile = blast_data.get('data', {}) 
                
                for locus_name, repeat_count in profile.items():
                    if repeat_count is None or repeat_count == "No Primer Hit":
                        continue

                    # Since we used Contig.id as the FASTA header, mlva.py 
                    # should have associated the hit with that ID.
                    # We pick a representative contig or the one BLAST matched.
                    # For simplicity, we get the first contig of the isolate 
                    # TODO: extend mlva.py to return the specific contig header.
                    target_contig = isolate.contigs.first() 
                    
                    _create_locus_result(
                        mlva_config, 
                        target_contig, 
                        locus_name, 
                        str(repeat_count)
                    )
                    mlva_profile_count += 1
            
            # Update job status
            if mlva_config and job:
                mlva_config.jobs[job.id] = {
                    'rq_job_id': job.id,
                    'status': 'completed',
                    'created_at': mlva_config.jobs.get(job.id, {}).get('created_at', datetime.now().isoformat()),
                    'completed_at': datetime.now().isoformat(),
                }
                mlva_config.save(update_fields=['jobs'])
            
            progress_tracker.set_stage('completed', 'Analysis completed successfully')
            return {'status': 'completed', 'count': mlva_profile_count}
                
    except Exception as e:
        logger.error(f"Analysis failed: {str(e)}")
        if mlva_config and job:
            mlva_config.jobs[job.id] = {
                'rq_job_id': job.id,
                'status': 'failed',
                'error_message': str(e),
                'completed_at': datetime.now().isoformat(),
            }
            mlva_config.save(update_fields=['jobs'])
        raise


def coordinate_mlva_analysis(mlva_config_id: int):
    """
    Orchestrator: Breaks the analysis into many small jobs.
    """
    job = get_current_job()
    progress = ProgressTracker(job)
    progress.set_stage('coordinating', 'Starting MLVA analysis coordination')
    
    mlva_config = MLVAConfig.objects.get(id=mlva_config_id)
    queue = django_rq.get_queue('default')
    vntrs = _extract_vntrs_from_locus_set(mlva_config.locus_set)
    
    isolates = list(mlva_config.investigation.isolates.all())
    progress.update_assembly_progress(0, len(isolates), message=f"Queuing {len(isolates)} isolate jobs")

    for idx, isolate in enumerate(isolates):        
        job_id = f"blast_isolate_{isolate.id}_{mlva_config_id}"
        rq_job = queue.enqueue(
            run_isolate_blast_task,
            mlva_config_id=mlva_config_id,
            isolate_id=isolate.id,
            vntrs=vntrs,
            job_id=job_id
        )
        mlva_config.jobs[rq_job.id] = {
            'type': 'blast_isolate',
            'isolate_id': isolate.id,
            'status': 'queued',
            'created_at': timezone.now().isoformat()
        }
        progress.update_assembly_progress(idx + 1, len(isolates), isolate.name, f"Queued isolate {isolate.name}")
    
    mlva_config.save(update_fields=['jobs'])
    
    # Update coordinator job status to completed
    mlva_config.refresh_from_db()
    if job.id in mlva_config.jobs:
        mlva_config.jobs[job.id]['status'] = 'completed'
        mlva_config.jobs[job.id]['completed_at'] = timezone.now().isoformat()
        mlva_config.save(update_fields=['jobs'])
    
    progress.set_stage('completed', 'All isolate jobs queued successfully')


def run_isolate_blast_task(mlva_config_id, isolate_id, vntrs):
    """
    Worker: Performs BLAST for a single isolate and saves LocusResults.
    """   
    job = get_current_job()
    progress = ProgressTracker(job)
    progress.set_stage('blast', f'Processing isolate {isolate_id}')
    
    mlva_config = MLVAConfig.objects.get(id=mlva_config_id)
    isolate = Isolate.objects.get(id=isolate_id)

    with tempfile.TemporaryDirectory() as temp_dir:
        progress.update_assembly_progress(0, 5, isolate.name, 'Generating primers FASTA')
        # Generate primers FASTA from LocusSet
        primers_file = _build_primers_fasta_from_locus_set(mlva_config.locus_set, temp_dir)
        
        progress.update_assembly_progress(1, 5, isolate.name, 'Creating isolate FASTA')
        # Create isolate fasta
        isolate_fasta = os.path.join(temp_dir, f"isolate_{isolate.id}.fna")
        with open(isolate_fasta, 'w') as f:
            for contig in isolate.contigs.all():
                f.write(f">{contig.id}\n{contig.sequence}\n")

        progress.update_assembly_progress(2, 5, isolate.name, 'Running VNTR detection')
        data, _ = mlva.find_all_vntrs(isolate_fasta, primers_file, vntrs)

        progress.update_assembly_progress(3, 5, isolate.name, 'Saving results')
        # Process results immediately
        for locus_name, repeat_count in data.items():
            if repeat_count is None or repeat_count == "No Primer Hit":
                continue
            
            target_contig = isolate.contigs.first() 
            _create_locus_result(mlva_config, target_contig, locus_name, str(repeat_count))

        progress.update_assembly_progress(5, 5, isolate.name, 'Task completed')

    # Update sub-job status in the config with row locking to prevent race conditions
    # when multiple workers write to the 'jobs' JSONField concurrently.   
    with transaction.atomic():
        mlva_config = MLVAConfig.objects.select_for_update().get(id=mlva_config.id)
        if job.id in mlva_config.jobs:
            mlva_config.jobs[job.id]['status'] = 'completed'
            mlva_config.jobs[job.id]['completed_at'] = timezone.now().isoformat()
            mlva_config.save(update_fields=['jobs'])
    
    progress.set_stage('completed', f'Isolate {isolate.name} analysis completed')