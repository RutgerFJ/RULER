"""Progress tracking utilities for background tasks."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from rq.job import Job
import django_rq
import logging

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Manages progress tracking for a single long-running RQ task."""
    
    def __init__(self, job: Job):
        """Initialize progress tracker for a job.
        
        Args:
            job: RQ Job object to track progress for
        """
        self.job = job
        self.start_time = datetime.now(timezone.utc)
        # Initialize metadata structure - ensure it's properly set on the job
        if not self.job.meta:
            self.job.meta = {}
        
        # Always initialize key fields if they don't exist
        if 'stage' not in self.job.meta:
            self.job.meta['stage'] = 'pending'
        if 'stage_description' not in self.job.meta:
            self.job.meta['stage_description'] = ''
        if 'progress_percentage' not in self.job.meta:
            self.job.meta['progress_percentage'] = 0
        if 'messages' not in self.job.meta:
            self.job.meta['messages'] = []
        
        # Save the initialized metadata to Redis
        self.job.save_meta()
    
    def set_stage(self, stage: str, description: str = "") -> None:
        """Set current stage of processing.
        
        Args:
            stage: Stage name (e.g., 'blast', 'mlva', 'strd')
            description: Optional description of current activity
        """
        self.job.meta['stage'] = stage
        self.job.meta['stage_description'] = description
        self.job.save_meta()
    
    def update_assembly_progress(
        self,
        current: int,
        total: int,
        assembly_name: str = "",
        message: str = ""
    ) -> None:
        """Update progress for assembly processing.
        
        Args:
            current: Current assembly index (0-based)
            total: Total number of assemblies
            assembly_name: Name/ID of current assembly
            message: Optional progress message
        """
        progress_pct = int((current / total) * 100) if total > 0 else 0
        
        self.job.meta['assemblies_completed'] = current
        self.job.meta['total_assemblies'] = total
        self.job.meta['current_assembly'] = assembly_name
        self.job.meta['progress_percentage'] = progress_pct
        
        if message:
            if 'messages' not in self.job.meta:
                self.job.meta['messages'] = []
            self.job.meta['messages'].append({
                'timestamp': datetime.now().isoformat(),
                'message': message
            })
        
        self.job.save_meta()
    
    def get_progress_dict(self) -> Dict[str, Any]:
        """Get current progress state as dict.
        
        Returns:
            Dictionary with progress information
        """
        stage = self.job.meta.get('stage', 'pending')
        progress_pct = self.job.meta.get('progress_percentage', 0)
        assemblies_completed = self.job.meta.get('assemblies_completed', 0)
        total_assemblies = self.job.meta.get('total_assemblies', 0)
        
        # Use completion time if job is done, otherwise use now
        end_time = datetime.now(timezone.utc)
        if progress_pct == 100:
            # Try to get the completed_at time from metadata
            completed_at_str = self.job.meta.get('completed_at')
            if completed_at_str:
                try:
                    end_time = datetime.fromisoformat(completed_at_str)
                except (ValueError, TypeError):
                    pass
        
        elapsed = end_time - self.start_time
        elapsed_seconds = int(elapsed.total_seconds())
        elapsed_str = self._format_duration(elapsed_seconds)
        
        # Estimate remaining time based on progress
        estimated_remaining = None
        if progress_pct > 0 and progress_pct < 100:
            estimated_total_seconds = int(elapsed_seconds / (progress_pct / 100))
            estimated_remaining = self._format_duration(
                estimated_total_seconds - elapsed_seconds
            )
        
        return {
            'stage': stage,
            'stage_description': self.job.meta.get('stage_description', ''),
            'stage_progress': f"{assemblies_completed}/{total_assemblies}",
            'overall_progress': progress_pct,
            'current_assembly': self.job.meta.get('current_assembly', ''),
            'elapsed_time': elapsed_str,
            'estimated_time_remaining': estimated_remaining,
        }
    
    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format seconds as human-readable duration.
        
        Args:
            seconds: Duration in seconds
            
        Returns:
            Formatted string (e.g., "2m 15s", "45s")
        """
        minutes = seconds // 60
        secs = seconds % 60
        
        if minutes == 0:
            return f"{secs}s"
        return f"{minutes}m {secs}s"


class MLVAConfigProgressTracker:
    """Aggregates progress tracking for all jobs related to an MLVAConfig.
    
    Fetches progress data from all jobs stored in MLVAConfig.jobs JSONField
    and aggregates them to provide overall progress information.
    """
    
    def __init__(self, mlva_config):
        """Initialize progress tracker for an MLVAConfig.
        
        Args:
            mlva_config: MLVAConfig instance with jobs JSONField
        """
        self.mlva_config = mlva_config
        self.connection = django_rq.get_connection()
        self._fetch_all_job_data()
    
    def _fetch_all_job_data(self) -> None:
        """Fetch RQ job data for all jobs in MLVAConfig.
        
        Populates self.job_data_map with RQ job metadata for each job ID.
        """
        self.job_data_map = {}  # Maps RQ job ID to job metadata
        self.job_progress_map = {}  # Maps RQ job ID to progress dict
        
        if not self.mlva_config.jobs:
            return
        
        for rq_job_id in self.mlva_config.jobs.keys():
            try:
                rq_job = Job.fetch(rq_job_id, connection=self.connection)
                if rq_job:
                    self.job_data_map[rq_job_id] = rq_job
                    # Extract progress metadata from the job
                    if rq_job.meta:
                        self.job_progress_map[rq_job_id] = rq_job.meta.copy()
            except Exception as e:
                logger.warning(f"Could not fetch job {rq_job_id}: {str(e)}")
    
    def get_aggregated_progress(self) -> Dict[str, Any]:
        """Get aggregated progress across all jobs.
        
        Returns a dictionary with overall progress information combining
        data from all jobs in the MLVAConfig.
        
        Returns:
            Dictionary with aggregated progress information
        """
        if not self.mlva_config.jobs:
            return {
                'stage': 'pending',
                'stage_description': 'No jobs started',
                'progress_percentage': 0,
                'stage_progress': '0/0',
                'jobs_completed': 0,
                'total_jobs': 0,
                'messages': [],
                'overall_progress': 0,
                'elapsed_time': '0s',
                'estimated_time_remaining': None,
            }
        
        total_jobs = len(self.mlva_config.jobs)
        
        # Count job statuses
        completed_jobs = 0
        failed_jobs = 0
        in_progress_jobs = 0
        queued_jobs = 0
        
        all_messages = []
        max_progress = 0
        current_stage = 'pending'
        stage_description = ''
        
        for rq_job_id, job_metadata in self.mlva_config.jobs.items():
            job_status = job_metadata.get('status', 'unknown')
            
            if job_status == 'completed':
                completed_jobs += 1
            elif job_status == 'failed':
                failed_jobs += 1
            elif job_status in ['started', 'in_progress']:
                in_progress_jobs += 1
            elif job_status == 'queued':
                queued_jobs += 1
            
            # Collect progress data from individual jobs
            if rq_job_id in self.job_progress_map:
                progress = self.job_progress_map[rq_job_id]
                
                # Update current stage if any job is active
                if in_progress_jobs > 0:
                    stage = progress.get('stage', '')
                    if stage and stage != 'pending':
                        current_stage = stage
                        stage_description = progress.get('stage_description', '')
                
                # Track max progress
                job_progress_pct = progress.get('progress_percentage', 0)
                if job_progress_pct > max_progress:
                    max_progress = job_progress_pct
                
                # Collect messages from all jobs
                job_messages = progress.get('messages', [])
                if job_messages:
                    all_messages.extend(job_messages)
        
        # Calculate overall progress percentage
        # If any jobs are completed, give them 100% credit
        # If any are in progress, give them their current % (0-100)
        # Otherwise 0%
        if completed_jobs == total_jobs:
            overall_progress = 100
            current_stage = 'completed'
            stage_description = 'All jobs completed successfully'
        elif failed_jobs > 0:
            overall_progress = int((completed_jobs / total_jobs) * 100)
            current_stage = 'failed'
            stage_description = f'{failed_jobs} job(s) failed'
        elif in_progress_jobs > 0:
            # Average progress of in-progress jobs, add completed jobs
            progress_from_complete = completed_jobs * 100
            progress_from_inprogress = in_progress_jobs * max_progress
            overall_progress = int((progress_from_complete + progress_from_inprogress) / total_jobs)
        else:
            overall_progress = int((completed_jobs / total_jobs) * 100)
        
        # Sort messages by timestamp (most recent first)
        try:
            all_messages.sort(
                key=lambda m: m.get('timestamp', ''),
                reverse=True
            )
        except Exception:
            pass
        
        # Calculate elapsed time from first job
        start_time = None
        for job_metadata in self.mlva_config.jobs.values():
            created_at_str = job_metadata.get('created_at')
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str)
                    # Ensure timezone-aware: if naive, assume UTC
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    if start_time is None or created_at < start_time:
                        start_time = created_at
                except (ValueError, TypeError):
                    pass
        
        elapsed_str = '0s'
        estimated_remaining = None
        if start_time:
            # Determine end time: use completion time if finished, otherwise use now
            end_time = datetime.now(timezone.utc)
            if overall_progress == 100:
                # Analysis is complete - find the latest completion time
                latest_completed_at = None
                for job_metadata in self.mlva_config.jobs.values():
                    completed_at_str = job_metadata.get('completed_at')
                    if completed_at_str:
                        try:
                            completed_at = datetime.fromisoformat(completed_at_str)
                            # Ensure timezone-aware: if naive, assume UTC
                            if completed_at.tzinfo is None:
                                completed_at = completed_at.replace(tzinfo=timezone.utc)
                            if latest_completed_at is None or completed_at > latest_completed_at:
                                latest_completed_at = completed_at
                        except (ValueError, TypeError):
                            pass
                if latest_completed_at:
                    end_time = latest_completed_at
            
            elapsed = end_time - start_time
            elapsed_seconds = int(elapsed.total_seconds())
            elapsed_str = self._format_duration(elapsed_seconds)
            
            # Estimate remaining time (only if still in progress)
            if overall_progress > 0 and overall_progress < 100:
                estimated_total_seconds = int(elapsed_seconds / (overall_progress / 100))
                estimated_remaining = self._format_duration(
                    estimated_total_seconds - elapsed_seconds
                )
        
        return {
            'stage': current_stage,
            'stage_description': stage_description,
            'progress_percentage': overall_progress,
            'stage_progress': f"{completed_jobs}/{total_jobs}",
            'jobs_completed': completed_jobs,
            'total_jobs': total_jobs,
            'jobs_failed': failed_jobs,
            'jobs_in_progress': in_progress_jobs,
            'jobs_queued': queued_jobs,
            'messages': all_messages[-10:],  # Last 10 messages
            'overall_progress': overall_progress,
            'elapsed_time': elapsed_str,
            'estimated_time_remaining': estimated_remaining,
        }
    
    def get_job_details(self) -> List[Dict[str, Any]]:
        """Get detailed information about each job.
        
        Returns:
            List of dicts with job details (status, progress, etc.)
        """
        job_details = []
        
        for rq_job_id, job_metadata in self.mlva_config.jobs.items():
            job_info = {
                'rq_job_id': rq_job_id,
                'type': job_metadata.get('type', 'unknown'),
                'status': job_metadata.get('status', 'unknown'),
                'created_at': job_metadata.get('created_at'),
                'completed_at': job_metadata.get('completed_at'),
                'error_message': job_metadata.get('error_message'),
                'isolate_id': job_metadata.get('isolate_id'),
            }
            
            # Add progress data if available
            if rq_job_id in self.job_progress_map:
                progress = self.job_progress_map[rq_job_id]
                job_info['progress_percentage'] = progress.get('progress_percentage', 0)
                job_info['stage'] = progress.get('stage', '')
                job_info['stage_description'] = progress.get('stage_description', '')
            
            job_details.append(job_info)
        
        return job_details
    
    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format seconds as human-readable duration.
        
        Args:
            seconds: Duration in seconds
            
        Returns:
            Formatted string (e.g., "2m 15s", "45s")
        """
        minutes = seconds // 60
        secs = seconds % 60
        
        if minutes == 0:
            return f"{secs}s"
        return f"{minutes}m {secs}s"

