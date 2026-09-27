from datetime import datetime
import json
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.contrib import messages
from django.utils import timezone
from django.template.loader import render_to_string

import django_rq

from core.forms import (
    SignUpForm, LoginForm,
    BasicInvestigationForm, AssemblyUploadForm, MetadataUploadForm,
    MLVAConfigPrimerSelectionForm, MLVAConfigCountModeForm, LocusSetCreationForm
)
from core.models import Investigation, UserInvestigation, MLVAConfig, LocusSet, Locus, Isolate
from core.progress import MLVAConfigProgressTracker
from core.color_schemes import get_available_schemes, get_color_for_value, get_gradient_css


def _get_friendly_job_type(job_type):
    """Convert job type identifier to user-friendly name.
    
    Args:
        job_type: Job type identifier (e.g., 'blast_isolate')
        
    Returns:
        User-friendly job type name (e.g., 'BLAST isolate')
    """
    type_mapping = {
        'blast_isolate': 'BLAST isolate',
        'mlva': 'MLVA analysis',
        'strd': 'STRD calculation',
    }
    return type_mapping.get(job_type, job_type.replace('_', ' ').title() if job_type else 'Unknown')


def _sort_profiles_rows(rows, sort_by, sort_order):
    """Sort profile rows by the specified field and order.
    
    Args:
        rows: List of row dictionaries from profiles_table
        sort_by: Field to sort by ('isolate', 'sampling_location', 'sampling_date', 
                 'assembly_size', 'contig_count', 'n50', 'closest_strd')
        sort_order: 'asc' or 'desc'
        
    Returns:
        Sorted list of rows
    """    
    def extract_numeric(val):
        """Extract numeric value from a string, handling units like '4.08 Mb'."""
        if val is None:
            return float('-inf')
        if isinstance(val, (int, float)):
            return float(val)
        
        # Try to extract numeric part from string (e.g., "4.08 Mb" -> 4.08)
        try:
            match = re.search(r'([\d.]+)', str(val))
            if match:
                return float(match.group(1))
        except (ValueError, AttributeError):
            pass
        
        return float('-inf')
    
    def get_sort_key(row):
        metadata = row.get('metadata', {})
        
        if sort_by == 'isolate':
            # Alphanumeric sort for isolate name
            return (row.get('isolate', '').lower(),)
        elif sort_by == 'sampling_location':
            # Alphanumeric sort, with None/empty at end
            val = metadata.get('sampling_location')
            return ((1, val.lower()) if val else (0, ''),)
        elif sort_by == 'sampling_date':
            # Date sort, with None/empty at end
            val = metadata.get('sampling_date')
            if val:
                try:
                    if isinstance(val, str):
                        date_obj = datetime.fromisoformat(val.split('T')[0])
                    else:
                        date_obj = val
                    return (1, date_obj)
                except:
                    return (0, datetime.max)
            return (0, datetime.min)
        elif sort_by == 'assembly_size':
            # Numeric sort, extracting from strings like "4.08 Mb"
            val = metadata.get('assembly_size')
            return (extract_numeric(val),)
        elif sort_by == 'contig_count':
            # Numeric sort
            val = metadata.get('contig_count')
            return (extract_numeric(val),)
        elif sort_by == 'n50':
            # Numeric sort
            val = metadata.get('n50')
            return (extract_numeric(val),)
        elif sort_by == 'closest_strd':
            # Numeric sort of closest_strd
            closest_strd = metadata.get('closest_strd', {})
            val = closest_strd.get('min_strd')
            return (extract_numeric(val) if val is not None else float('inf'),)
        elif sort_by == 'total_repeats':
            # Numeric sort of total_repeats
            val = metadata.get('total_repeats')
            return (extract_numeric(val) if val is not None else float('inf'),)
        else:
            return (0,)
    
    reverse = sort_order == 'desc'
    return sorted(rows, key=get_sort_key, reverse=reverse)


def _queue_mlva_analysis_job(mlva_config, user):
    """
    Queue an MLVA analysis job using django-rq.
    
    Args:
        mlva_config: MLVAConfig instance to analyze
        user: User instance initiating the analysis
        
    Returns:
        Dictionary with job information
    """
    queue = django_rq.get_queue('default')
    
    # Queue the analysis coordination task
    job = queue.enqueue(
        'core.tasks.coordinate_mlva_analysis',
        mlva_config_id=mlva_config.id
    )
    
    # Track the job in MLVAConfig.jobs
    mlva_config.jobs[job.id] = {
        'rq_job_id': job.id,
        'status': 'queued',
        'user_id': user.id,
        'created_at': timezone.now().isoformat(),
    }
    mlva_config.save(update_fields=['jobs'])
    
    return {
        'job_id': job.id,
        'status': 'queued'
    }


@login_required(login_url='login')
@require_http_methods(["GET"])
def primer_selection_method_partial(request, investigation_id):
    """
    HTMX endpoint: Return the appropriate primer selection form section.
    Renders 'existing method', 'upload method', or 'create new method' based on query param.
    """
    # Verify user has access to investigation
    investigation = get_object_or_404(Investigation, id=investigation_id)
    get_object_or_404(
        UserInvestigation,
        user=request.user,
        investigation=investigation
    )
    
    method = request.GET.get('method', 'existing')
    form = MLVAConfigPrimerSelectionForm()
    
    if method == 'existing':
        return render(request, 'partials/primer_selection_existing.html', {
            'form': form,
        })
    elif method == 'create_new':
        return render(request, 'partials/primer_selection_create_new.html', {
            'form': form,
        })
    else:
        return HttpResponse("Invalid method", status=400)


@login_required(login_url='login')
@require_http_methods(["GET", "POST"])
def create_mlvaconfig(request, investigation_id):
    """
    Multi-step wizard for MLVA config creation within an investigation.
    
    Steps:
    1. Select existing primer set or upload new one
    2. Choose count mode (uncounted/counted)
    3. Review and confirm settings
    4. Show completion and analysis start option
    
    Uses Django sessions to persist data across steps.
    """
    # Verify user has access to investigation
    investigation = get_object_or_404(Investigation, id=investigation_id)
    user_inv = get_object_or_404(
        UserInvestigation,
        user=request.user,
        investigation=investigation
    )
    if not user_inv.can_edit():
        messages.error(request, "You don't have permission to edit this investigation.")
        return redirect('investigations_detail', investigation_id=investigation_id)
    
    # Clear any pending messages to avoid carrying over messages from previous operations
    # (e.g., "Analysis job started successfully!" from a previous MLVAConfig creation)
    storage = messages.get_messages(request)
    for message in storage:
        pass  # Iterate through to mark all messages as consumed
    
    # Initialize session data structure if needed
    session_key = f'mlvaconfig_creation_{investigation_id}'
    if session_key not in request.session:
        request.session[session_key] = {
            'step': 1,
            'data': {},
            'max_completed_step': 0,
        }
    
    session_data = request.session[session_key]
    current_step = session_data.get('step', 1)
    max_completed = session_data.get('max_completed_step', 0)
    
    # Prevent invalid steps
    if current_step < 1:
        session_data['step'] = 1
        request.session.modified = True
        return redirect('create_mlvaconfig', investigation_id=investigation_id)
    
    if current_step > 4:
        session_data['step'] = 4
        request.session.modified = True
        return redirect('create_mlvaconfig', investigation_id=investigation_id)
    
    # Prevent skipping ahead
    if current_step > max_completed + 1:
        session_data['step'] = max_completed + 1
        request.session.modified = True
        return redirect('create_mlvaconfig', investigation_id=investigation_id)
    
    if request.method == 'POST':
        # Handle explicit back action
        if request.POST.get('action') == 'back':
            session_data['step'] = max(current_step - 1, 1)
            request.session.modified = True
            return redirect('create_mlvaconfig', investigation_id=investigation_id)
        
        # Process form submission
        return _process_mlvaconfig_creation_step(request, investigation_id, current_step, session_data)
    
    # GET request - render current step
    return _render_mlvaconfig_creation_step(request, investigation_id, current_step)


def _render_mlvaconfig_creation_step(request, investigation_id, step):
    """Render the appropriate form for the current step."""
    session_key = f'mlvaconfig_creation_{investigation_id}'
    session_data = request.session.get(session_key, {})
    data = session_data.get('data', {})
    investigation = Investigation.objects.get(id=investigation_id)
    
    context = {
        'step': step,
        'total_steps': 4,
        'investigation_id': investigation_id,
        'investigation_name': investigation.name,
        'investigation_isolate_count': investigation.isolates.count()
    }
    
    # Calculate progress bar percentage
    progress_percent_map = {1: 12.5, 2: 37.5, 3: 62.5, 4: 100}
    context['progress_percent'] = progress_percent_map.get(step, 0)
    
    if step == 1:
        form = MLVAConfigPrimerSelectionForm()
        context['form'] = form
        context['step_title'] = 'Select Primer Set'
        
    elif step == 2:
        form = MLVAConfigCountModeForm(initial={
            'count_mode': data.get('count_mode', 'uncounted'),
        })
        context['form'] = form
        context['step_title'] = 'Choose Counting Mode'
        context['primer_set_name'] = data.get('primer_set_name', '')
        context['locus_count'] = data.get('locus_count', 0)
        
    elif step == 3:
        context['step_title'] = 'Review Settings'
        context['primer_set_name'] = data.get('primer_set_name', '')
        context['locus_count'] = data.get('locus_count', 0)
        context['count_mode_display'] = dict(MLVAConfigCountModeForm.COUNT_MODE_CHOICES).get(
            data.get('count_mode', 'uncounted')
        )
        
    elif step == 4:
        # Completion step
        context['mlva_config_id'] = data.get('mlva_config_id')
        context['primer_set_name'] = data.get('primer_set_name', '')
        context['count_mode_display'] = dict(MLVAConfigCountModeForm.COUNT_MODE_CHOICES).get(
            data.get('count_mode', 'uncounted')
        )
    
    return render(request, 'create_mlvaconfig.html', context)


def _process_mlvaconfig_creation_step(request, investigation_id, step, session_data):
    """Process form submission for current step."""
    data = session_data.get('data', {})
    
    if step == 1:
        return _process_mlvaconfig_step_1_primers(request, investigation_id, session_data, data)
    elif step == 2:
        return _process_mlvaconfig_step_2_count_mode(request, investigation_id, session_data, data)
    elif step == 3:
        return _process_mlvaconfig_step_3_confirmation(request, investigation_id, session_data, data)
    elif step == 4:
        return _process_mlvaconfig_step_4_finalization(request, investigation_id, session_data, data)


def _process_mlvaconfig_step_1_primers(request, investigation_id, session_data, data):
    """Process step 1: Primer set selection or upload."""
    form = MLVAConfigPrimerSelectionForm(request.POST, request.FILES)
    
    if not form.is_valid():
        # Re-render with errors
        context = {
            'step': 1,
            'total_steps': 4,
            'progress_percent': 12.5,
            'form': form,
            'step_title': 'Select Primer Set',
            'investigation_id': investigation_id,
            'investigation_name': Investigation.objects.get(id=investigation_id).name,
        }
        return render(request, 'create_mlvaconfig.html', context)
    
    try:
        selection_method = form.cleaned_data['selection_method']
        
        if selection_method == 'existing':
            locus_set = form.cleaned_data['existing_primer_set']
            data['locus_set_id'] = locus_set.id
            data['primer_set_name'] = locus_set.name
            data['locus_count'] = locus_set.loci.count()
            data['is_new_locus_set'] = False
            
        elif selection_method == 'upload':
            # Parse primers from form
            if not hasattr(form, 'parsed_primers'):
                raise ValueError("Form did not parse primer file correctly.")
            
            parsed_primers = form.parsed_primers
            
            # Create new LocusSet
            locus_set_name = f"custom_{investigation_id}_{request.user.id}"
            locus_set = LocusSet.objects.create(
                name=locus_set_name,
                description=f"Custom primer set for investigation {investigation_id}"
            )
            
            # Create Locus objects for each primer
            for locus_name, locus_data in parsed_primers.items():
                Locus.objects.create(
                    locus_set=locus_set,
                    name=locus_name,
                    forward_primer=locus_data['forward'],
                    reverse_primer=locus_data['reverse'],
                    repeat_sequence=locus_data['repeat']
                )
            
            data['locus_set_id'] = locus_set.id
            data['primer_set_name'] = locus_set.name
            data['locus_count'] = len(parsed_primers)
            data['is_new_locus_set'] = True
        
        elif selection_method == 'create_new':
            # Parse primers from form
            if not hasattr(form, 'parsed_primers_new'):
                raise ValueError("Form did not parse primer file correctly.")
            
            parsed_primers = form.parsed_primers_new
            locus_set_name = form.cleaned_data['new_locus_set_name']
            locus_set_description = form.cleaned_data['new_locus_set_description']
            
            # Create new LocusSet
            locus_set = LocusSet.objects.create(
                name=locus_set_name,
                description=locus_set_description
            )
            
            # Create Locus objects for each primer
            for locus_name, locus_data in parsed_primers.items():
                Locus.objects.create(
                    locus_set=locus_set,
                    name=locus_name,
                    forward_primer=locus_data['forward'],
                    reverse_primer=locus_data['reverse'],
                    repeat_sequence=locus_data['repeat']
                )
            
            data['locus_set_id'] = locus_set.id
            data['primer_set_name'] = locus_set.name
            data['locus_count'] = len(parsed_primers)
            data['is_new_locus_set'] = True
        
        session_data['data'] = data
        session_data['step'] = 2
        session_data['max_completed_step'] = 1
        session_key = f'mlvaconfig_creation_{investigation_id}'
        request.session[session_key] = session_data
        request.session.modified = True
        request.session.save()  # Explicitly save the session
        
        return redirect('create_mlvaconfig', investigation_id=investigation_id)
    except Exception as e:
        
        messages.error(request, f"Error processing primer selection: {str(e)}")
        context = {
            'step': 1,
            'total_steps': 4,
            'progress_percent': 12.5,
            'form': form,
            'step_title': 'Select Primer Set',
            'investigation_id': investigation_id,
            'investigation_name': Investigation.objects.get(id=investigation_id).name,
        }
        return render(request, 'create_mlvaconfig.html', context)


def _process_mlvaconfig_step_2_count_mode(request, investigation_id, session_data, data):
    """Process step 2: Count mode selection."""
    form = MLVAConfigCountModeForm(request.POST)
    
    if form.is_valid():
        data['count_mode'] = form.cleaned_data['count_mode']
        session_data['data'] = data
        session_data['step'] = 3
        session_data['max_completed_step'] = 2
        session_key = f'mlvaconfig_creation_{investigation_id}'
        request.session[session_key] = session_data
        request.session.modified = True
        request.session.save()  # Explicitly save the session
        
        return redirect('create_mlvaconfig', investigation_id=investigation_id)
    else:
        # Re-render with errors
        context = {
            'step': 2,
            'total_steps': 4,
            'progress_percent': 37.5,
            'form': form,
            'step_title': 'Choose Counting Mode',
            'investigation_id': investigation_id,
            'investigation_name': Investigation.objects.get(id=investigation_id).name,
            'primer_set_name': data.get('primer_set_name', ''),
            'locus_count': data.get('locus_count', 0),
        }
        return render(request, 'create_mlvaconfig.html', context)


def _process_mlvaconfig_step_3_confirmation(request, investigation_id, session_data, data):
    """Process step 3: User confirmation - create MLVA configuration."""
    # Check for duplicate MLVA configuration before attempting to create
    investigation = Investigation.objects.get(id=investigation_id)
    locus_set = LocusSet.objects.get(id=data['locus_set_id'])
    count_mode = data['count_mode']
    
    existing_config = MLVAConfig.objects.filter(
        investigation=investigation,
        locus_set=locus_set,
        count_mode=count_mode
    ).first()
    
    if existing_config:
        error_msg = (
            f"An MLVA configuration with these exact settings "
            f"(primer set '{data.get('primer_set_name', 'Unknown')}' "
            f"and counting mode '{data.get('count_mode', 'unknown')}') "
            f"already exists for this investigation. "
            f"Please go back to view it or select different settings."
        )
        messages.error(request, error_msg)
        context = {
            'step': 3,
            'total_steps': 4,
            'progress_percent': 62.5,
            'step_title': 'Review Settings',
            'investigation_id': investigation_id,
            'investigation_name': investigation.name,
            'primer_set_name': data.get('primer_set_name', ''),
            'locus_count': data.get('locus_count', 0),
            'count_mode_display': dict(MLVAConfigCountModeForm.COUNT_MODE_CHOICES).get(
                data.get('count_mode', 'uncounted')
            ),
        }
        return render(request, 'create_mlvaconfig.html', context)
    
    # Perform finalization immediately (create MLVAConfig)
    try:
        mlva_config_id = _finalize_mlvaconfig_creation(
            request.user, investigation_id, data
        )
        data['mlva_config_id'] = mlva_config_id
        session_data['data'] = data
        session_data['step'] = 4
        session_data['max_completed_step'] = 3
        session_key = f'mlvaconfig_creation_{investigation_id}'
        request.session[session_key] = session_data
        request.session.modified = True
        request.session.save()  # Explicitly save the session
        
        return redirect('create_mlvaconfig', investigation_id=investigation_id)
    except Exception as e:
        
        error_msg = str(e)
        if 'unique' in error_msg.lower():
            error_msg = (
                f"An MLVA configuration with these exact settings "
                f"(primer set '{data.get('primer_set_name', 'Unknown')}' "
                f"and counting mode '{data.get('count_mode', 'unknown')}') "
                f"already exists for this investigation. "
                f"Please go back to view it or select different settings."
            )
        
        messages.error(request, f"Error creating MLVA config: {error_msg}")
        context = {
            'step': 3,
            'total_steps': 4,
            'progress_percent': 62.5,
            'step_title': 'Review Settings',
            'investigation_id': investigation_id,
            'investigation_name': Investigation.objects.get(id=investigation_id).name,
            'primer_set_name': data.get('primer_set_name', ''),
            'locus_count': data.get('locus_count', 0),
            'count_mode_display': dict(MLVAConfigCountModeForm.COUNT_MODE_CHOICES).get(
                data.get('count_mode', 'uncounted')
            ),
        }
        return render(request, 'create_mlvaconfig.html', context)


def _process_mlvaconfig_step_4_finalization(request, investigation_id, session_data, data):
    """Process step 4: Finalization complete - show results."""
    
    # Handle user actions on completion page
    if request.POST.get('view_config'):
        # User wants to start analysis - queue the job and redirect to detail view
        session_key = f'mlvaconfig_creation_{investigation_id}'
        
        try:
            mlva_config_id = data.get('mlva_config_id')
            mlva_config = get_object_or_404(MLVAConfig, id=mlva_config_id)
            
            job_info = _queue_mlva_analysis_job(mlva_config, request.user)
            
            if session_key in request.session:
                del request.session[session_key]
                request.session.modified = True
            
            messages.success(request, 'Analysis job started successfully!')
            return redirect('mlva_detail', 
                           investigation_id=investigation_id,
                           mlva_config_id=mlva_config_id)
        except Exception as e:
            messages.error(request, f'Error starting analysis: {str(e)}')
            return redirect('mlva_detail', 
                           investigation_id=investigation_id,
                           mlva_config_id=data.get('mlva_config_id'))
    
    if request.POST.get('create_new'):
        # User wants to create another config - clear session and redirect
        session_key = f'mlvaconfig_creation_{investigation_id}'
        if session_key in request.session:
            del request.session[session_key]
            request.session.modified = True
        
        # Clear any pending messages to avoid carrying over messages from previous operations
        storage = messages.get_messages(request)
        for message in storage:
            pass  # Iterate through to mark all messages as consumed
        
        return redirect('create_mlvaconfig', investigation_id=investigation_id)
    
    if request.POST.get('back_to_investigation'):
        # User wants to go back - clear session and redirect
        session_key = f'mlvaconfig_creation_{investigation_id}'
        if session_key in request.session:
            del request.session[session_key]
            request.session.modified = True
        return redirect('investigations_detail', investigation_id=investigation_id)
    
    # Initial GET render of step 4 - show completion page
    context = {
        'step': 4,
        'total_steps': 4,
        'progress_percent': 100,
        'investigation_id': investigation_id,
        'investigation_name': Investigation.objects.get(id=investigation_id).name,
        'mlva_config_id': data.get('mlva_config_id'),
        'primer_set_name': data.get('primer_set_name', ''),
        'count_mode_display': dict(MLVAConfigCountModeForm.COUNT_MODE_CHOICES).get(
            data.get('count_mode', 'uncounted')
        ),
    }
    
    return render(request, 'create_mlvaconfig.html', context)


def _finalize_mlvaconfig_creation(user, investigation_id, data):
    """
    Create MLVAConfig record.
    Returns mlva_config_id.
    """
    from django.db import IntegrityError
    
    investigation = Investigation.objects.get(id=investigation_id)
    locus_set = LocusSet.objects.get(id=data['locus_set_id'])
    
    try:
        with transaction.atomic():
            mlva_config = MLVAConfig.objects.create(
                investigation=investigation,
                locus_set=locus_set,
                count_mode=data['count_mode']
            )
            return mlva_config.id
    except IntegrityError as e:
        if 'unique' in str(e).lower():
            # A config with these exact settings already exists
            # Retrieve it and use that instead (outside atomic block)
            existing_config = MLVAConfig.objects.get(
                investigation=investigation,
                locus_set=locus_set,
                count_mode=data['count_mode']
            )
            return existing_config.id
        else:
            # Some other integrity error
            raise


@login_required(login_url='login')
@require_http_methods(["GET", "POST"])
def start_mlva_analysis(request, investigation_id, mlva_config_id):
    """
    Start MLVA analysis for a given MLVAConfig.
    Queues the background job and returns updated progress tracker.
    """
    # Verify user has access
    investigation = get_object_or_404(Investigation, id=investigation_id)
    get_object_or_404(
        UserInvestigation,
        user=request.user,
        investigation=investigation
    )
    
    mlva_config = get_object_or_404(
        MLVAConfig,
        id=mlva_config_id,
        investigation=investigation
    )
    
    try:
        job_info = _queue_mlva_analysis_job(mlva_config, request.user)

        # Load updated progress data
        progress_tracker = MLVAConfigProgressTracker(mlva_config)
        progress = progress_tracker.get_aggregated_progress()
        job_details = progress_tracker.get_job_details()

        context = {
            'mlva_config': mlva_config,
            'progress': progress,
            'job_details': job_details
        }
        return render(request, 'partials/mlva_progress_tracker.html', context)
        
    except Exception as e:
        messages.error(request, f"Error starting analysis: {str(e)}")
        return HttpResponse(f"Error: {str(e)}", status=400)



@login_required(login_url='login')
@require_http_methods(["GET"])
def get_mlva_progress(request, investigation_id, mlva_config_id):
    mlva_config = get_object_or_404(MLVAConfig, id=mlva_config_id)
    investigation = get_object_or_404(Investigation, id=investigation_id)

    # Verify user has access to the investigation this MLVA config belongs to
    get_object_or_404(
        UserInvestigation,
        user=request.user,
        investigation=investigation
    )

    # Load progress data for the MLVA config
    progress_tracker = MLVAConfigProgressTracker(mlva_config)
    progress = progress_tracker.get_aggregated_progress()
    job_details = progress_tracker.get_job_details()
    
    # Filter out coordinator/unknown jobs and convert types to friendly names
    job_details = [
        {**job, 'type': _get_friendly_job_type(job.get('type'))}
        for job in job_details 
        if job.get('type') != 'unknown'
    ]

    context = {
        'mlva_config': mlva_config,
        'investigation_id': investigation_id,
        'mlva_config_id': mlva_config_id,
        'progress': progress,
        'job_details': job_details
    }
    
    # If analysis is complete, load results data
    if progress.get('overall_progress') == 100:
        # Get color scheme from user profile
        color_scheme = request.user.mlva_profile.get_color_scheme()

        # Load profiles data with selected color scheme
        profiles_table = mlva_config.profiles_table(color_scheme=color_scheme)
        
        # Load pairwise distance matrix data and format for template
        pairwise_raw = mlva_config.calculate_pairwise_distances()
        
        # Build column headers as list of dicts with id and name
        column_headers = [
            {'id': iso_id, 'name': pairwise_raw['isolate_names'][iso_id]}
            for iso_id in pairwise_raw['isolates']
        ]
        
        # Calculate min and max distances (excluding diagonal and N/A)
        all_distances = []
        for iso_id_row in pairwise_raw['isolates']:
            for iso_id_col in pairwise_raw['isolates']:
                if iso_id_row != iso_id_col:
                    key = tuple(sorted([iso_id_row, iso_id_col]))
                    if key in pairwise_raw['distances']:
                        all_distances.append(pairwise_raw['distances'][key]['strd'])
        
        min_distance = min(all_distances) if all_distances else 0
        max_distance = max(all_distances) if all_distances else 0
        
        # Build a template-friendly matrix structure with color information
        pairwise_matrix = []
        for iso_id_row in pairwise_raw['isolates']:
            row = {'isolate_id': iso_id_row, 'isolate_name': pairwise_raw['isolate_names'][iso_id_row], 'distances': []}
            for iso_id_col in pairwise_raw['isolates']:
                if iso_id_row == iso_id_col:
                    row['distances'].append({
                        'value': '—',
                        'marked': False,
                        'color_class': 'bg-base-200',
                        'text_class': 'text-base-content/40',
                        'is_string': True
                    })
                else:
                    # Look up distance in both orderings
                    key = tuple(sorted([iso_id_row, iso_id_col]))
                    if key in pairwise_raw['distances']:
                        dist_data = pairwise_raw['distances'][key]
                        dist_value = dist_data['strd']
                        
                        # Use color scheme helper for distance coloring
                        bg_color = get_color_for_value(dist_value, min_distance, max_distance, color_scheme)
                        
                        row['distances'].append({
                            'value': dist_value,
                            'marked': dist_data['marked'],
                            'bg_color': bg_color,
                            'text_class': 'text-base-content'
                        })
                    else:
                        row['distances'].append({
                            'value': 'N/A',
                            'marked': False,
                            'color_class': 'bg-base-200',
                            'text_class': 'text-base-content/30',
                            'is_string': True
                        })
            pairwise_matrix.append(row)

        pairwise = mlva_config.calculate_pairwise_distances()

        nodes = [
            {
                'id': isolate_id,
                'label': pairwise['isolate_names'][isolate_id],
            }
            for isolate_id in pairwise['isolates']
        ]

        # D3 requires graph edges/links
        links = []

        for (iso1, iso2), distance_data in pairwise['distances'].items():
            links.append({
                'source': iso1,
                'target': iso2,
                'weight': distance_data['strd'],
                'marked': distance_data['marked'],
                'per_locus': distance_data['per_locus'],
            })
        
        context.update({
            'profiles_table': profiles_table,
            'profiles_gradient': get_gradient_css(color_scheme),
            'pairwise_data': {
                'column_headers': column_headers,
                'loci': pairwise_raw['loci'],
                'matrix': pairwise_matrix,
                'min_distance': min_distance,
                'max_distance': max_distance,
                'gradient': get_gradient_css(color_scheme),
            },
            'nodes': json.dumps(nodes),
            'links': json.dumps(links),
        })
    
    return render(request, 'partials/mlva_progress_tracker.html', context)


@login_required(login_url='login')
@require_http_methods(["GET"])
def mlva_detail(request, investigation_id, mlva_config_id):
    """
    View generic information for a specific MLVA configuration.
    Shows progress tracker while analysis is running, switches to profiles when complete.
    """
    mlva_config = get_object_or_404(MLVAConfig, id=mlva_config_id)
    
    # Verify user has access to the investigation this MLVA config belongs to
    get_object_or_404(
        UserInvestigation,
        user=request.user,
        investigation=mlva_config.investigation
    )
    
    # Load initial progress data for the MLVA config
    progress_tracker = MLVAConfigProgressTracker(mlva_config)
    progress = progress_tracker.get_aggregated_progress()
    job_details = progress_tracker.get_job_details()
    
    # Filter out coordinator/unknown jobs and convert types to friendly names
    job_details = [
        {**job, 'type': _get_friendly_job_type(job.get('type'))}
        for job in job_details 
        if job.get('type') != 'unknown'
    ]
    
    # Get color scheme from request or session, default to 'default'
    color_scheme = request.user.mlva_profile.get_color_scheme()
    request.session['mlva_color_scheme'] = color_scheme
    
    # Load profiles data with selected color scheme
    profiles_table = mlva_config.profiles_table(color_scheme=color_scheme)
    
    # Extract and validate sorting parameters
    sort_by = request.GET.get('sort_by', 'isolate')
    sort_order = request.GET.get('sort_order', 'asc')
    
    allowed_sorts = ['isolate', 'sampling_location', 'sampling_date', 'assembly_size', 'contig_count', 'n50', 'closest_strd', 'total_repeats']
    if sort_by not in allowed_sorts:
        sort_by = 'isolate'
    if sort_order not in ['asc', 'desc']:
        sort_order = 'asc'
    
    # Apply sorting to profiles table
    profiles_table['active_rows'] = _sort_profiles_rows(profiles_table['active_rows'], sort_by, sort_order)
    profiles_table['inactive_rows'] = _sort_profiles_rows(profiles_table['inactive_rows'], sort_by, sort_order)
    
    # Build next_sort_orders for template
    next_sort_orders = {
        col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
        for col in allowed_sorts
    }
    
    # Load pairwise distance matrix data and format for template
    pairwise_raw = mlva_config.calculate_pairwise_distances()
    
    # Separate active and inactive isolates
    active_isolate_ids = [iso.id for iso in mlva_config.investigation.isolates.filter(active=True).order_by('name')]
    inactive_isolate_ids = [iso.id for iso in mlva_config.investigation.isolates.filter(active=False).order_by('name')]
    
    # Build column headers as list of dicts with id and name (only for active isolates in main matrix)
    column_headers = [
        {'id': iso_id, 'name': pairwise_raw['isolate_names'][iso_id]}
        for iso_id in active_isolate_ids
    ]
    
    # Calculate min and max distances (excluding diagonal and N/A, only between active isolates)
    all_distances = []
    for iso_id_row in active_isolate_ids:
        for iso_id_col in active_isolate_ids:
            if iso_id_row != iso_id_col:
                key = tuple(sorted([iso_id_row, iso_id_col]))
                if key in pairwise_raw['distances']:
                    all_distances.append(pairwise_raw['distances'][key]['strd'])
    
    min_distance = min(all_distances) if all_distances else 0
    max_distance = max(all_distances) if all_distances else 0
    
    # Build active isolates matrix (only showing active isolates)
    pairwise_matrix = []
    for iso_id_row in active_isolate_ids:
        row = {'isolate_id': iso_id_row, 'isolate_name': pairwise_raw['isolate_names'][iso_id_row], 'distances': []}
        for iso_id_col in active_isolate_ids:
            if iso_id_row == iso_id_col:
                row['distances'].append({
                    'value': '—',
                    'marked': False,
                    'color_class': 'bg-base-200',
                    'text_class': 'text-base-content/40',
                    'is_string': True
                })
            else:
                # Look up distance in both orderings
                key = tuple(sorted([iso_id_row, iso_id_col]))
                if key in pairwise_raw['distances']:
                    dist_data = pairwise_raw['distances'][key]
                    dist_value = dist_data['strd']
                    
                    # Use color scheme helper for distance coloring
                    bg_color = get_color_for_value(dist_value, min_distance, max_distance, color_scheme)
                    
                    row['distances'].append({
                        'value': dist_value,
                        'marked': dist_data['marked'],
                        'bg_color': bg_color,
                        'text_class': 'text-base-content'
                    })
                else:
                    row['distances'].append({
                        'value': 'N/A',
                        'marked': False,
                        'color_class': 'bg-base-200',
                        'text_class': 'text-base-content/30',
                        'is_string': True
                    })
        pairwise_matrix.append(row)
    
    # Use inactive rows from profiles table for display in pairwise matrix
    pairwise_inactive_rows = profiles_table.get('inactive_rows', [])

    pairwise = mlva_config.calculate_pairwise_distances()

    # Get set of active isolate IDs for filtering tree view
    active_isolate_ids_set = set(
        mlva_config.investigation.isolates.filter(active=True).values_list('id', flat=True)
    )

    # Build enriched node data with isolate details (only for active isolates)
    nodes = []
    isolate_map = {iso.id: iso for iso in mlva_config.investigation.isolates.all()}
    
    for isolate_id in pairwise['isolates']:
        # Only include active isolates in tree view
        if isolate_id not in active_isolate_ids_set:
            continue
            
        isolate = isolate_map.get(isolate_id)
        contig_count = isolate.contig_count if isolate else 0
        
        # Get MLVA profile for this isolate (locus results)
        locus_results = {}
        if isolate:
            from core.models import LocusResult
            results = LocusResult.objects.filter(
                mlva_config=mlva_config,
                contig__isolate=isolate
            ).select_related('locus').order_by('locus__name')
            
            # Use a dict to keep only one result per locus (last one after ordering by locus name)
            seen_loci = {}
            for result in results:
                locus_name = result.locus.name
                if locus_name not in seen_loci:
                    locus_results[locus_name] = result.repeat_count
                    seen_loci[locus_name] = True
        
        node = {
            'id': isolate_id,
            'label': pairwise['isolate_names'][isolate_id],
            'isolate_name': isolate.name if isolate else '',
            'contig_count': contig_count,
            'locus_results': locus_results,
        }
        nodes.append(node)

    # D3 requires graph edges/links (only between active isolates)
    # D3 requires graph edges/links (only between active isolates)
    links = []

    for (iso1, iso2), distance_data in pairwise['distances'].items():
        # Only include links where both isolates are active
        if iso1 not in active_isolate_ids_set or iso2 not in active_isolate_ids_set:
            continue
            
        links.append({
            'source': iso1,
            'target': iso2,
            'weight': distance_data['strd'],
            'marked': distance_data['marked'],
            'per_locus': distance_data['per_locus'],
        })
    
    # Build list of inactive isolates for reactivation UI
    inactive_isolates = []
    for iso in mlva_config.investigation.isolates.filter(active=False).order_by('name'):
        inactive_isolates.append({
            'id': iso.id,
            'name': iso.name,
        })
    
    context = {
        'mlva_config': mlva_config,
        'investigation_id': investigation_id,
        'mlva_config_id': mlva_config_id,
        'progress': progress,
        'job_details': job_details,
        'profiles_table': profiles_table,
        'profiles_gradient': get_gradient_css(color_scheme),
        'sort_by': sort_by,
        'sort_order': sort_order,
        'next_sort_orders': next_sort_orders,
        'pairwise_data': {
            'column_headers': column_headers,
            'loci': pairwise_raw['loci'],
            'matrix': pairwise_matrix,
            'inactive_rows': pairwise_inactive_rows,
            'headers': profiles_table.get('headers', []),
            'min_distance': min_distance,
            'max_distance': max_distance,
            'gradient': get_gradient_css(color_scheme),
            'mlva_config_id': mlva_config_id,
            'investigation_id': investigation_id,
            'sort_by': sort_by,
            'sort_order': sort_order,
            'next_sort_orders': next_sort_orders,
            },
        'current_color_scheme': color_scheme,
        'nodes': json.dumps(nodes),
        'links': json.dumps(links),
        'inactive_isolates': json.dumps(inactive_isolates),
    }
    
    template = 'partials/mlva_profiles_tab.html' if request.headers.get('HX-Request') else 'mlva_detail.html'
    return render(request, template, context)


@login_required(login_url='login')
@require_http_methods(["GET"])
def get_mlva_tree_data(request, investigation_id, mlva_config_id):
    """Get tree view data (nodes and links) as JSON for dynamic tree reloading."""
    mlva_config = get_object_or_404(MLVAConfig, id=mlva_config_id)
    
    # Verify user has access to the investigation this MLVA config belongs to
    get_object_or_404(
        UserInvestigation,
        user=request.user,
        investigation=mlva_config.investigation
    )
    
    # Calculate pairwise distances
    pairwise = mlva_config.calculate_pairwise_distances()
    
    # Get set of active isolate IDs for filtering tree view
    active_isolate_ids_set = set(
        mlva_config.investigation.isolates.filter(active=True).values_list('id', flat=True)
    )
    
    # Build enriched node data with isolate details (only for active isolates)
    nodes = []
    isolate_map = {iso.id: iso for iso in mlva_config.investigation.isolates.all()}
    
    for isolate_id in pairwise['isolates']:
        # Only include active isolates in tree view
        if isolate_id not in active_isolate_ids_set:
            continue
            
        isolate = isolate_map.get(isolate_id)
        contig_count = isolate.contig_count if isolate else 0
        
        # Get MLVA profile for this isolate (locus results)
        locus_results = {}
        if isolate:
            from core.models import LocusResult
            results = LocusResult.objects.filter(
                mlva_config=mlva_config,
                contig__isolate=isolate
            ).select_related('locus').order_by('locus__name')
            
            # Use a dict to keep only one result per locus (last one after ordering by locus name)
            seen_loci = {}
            for result in results:
                locus_name = result.locus.name
                if locus_name not in seen_loci:
                    locus_results[locus_name] = result.repeat_count
                    seen_loci[locus_name] = True
        
        node = {
            'id': isolate_id,
            'label': pairwise['isolate_names'][isolate_id],
            'name': isolate.name,
            'sampling_location': isolate.sampling_location,
            'sampling_date': isolate.sampling_date,
            'contig_count': contig_count,
            'locus_results': locus_results,
        }
        nodes.append(node)

    # D3 requires graph edges/links (only between active isolates)
    links = []

    for (iso1, iso2), distance_data in pairwise['distances'].items():
        # Only include links where both isolates are active
        if iso1 not in active_isolate_ids_set or iso2 not in active_isolate_ids_set:
            continue
            
        links.append({
            'source': iso1,
            'target': iso2,
            'weight': distance_data['strd'],
            'marked': distance_data['marked'],
            'per_locus': distance_data['per_locus'],
        })
    
    # Build list of inactive isolates for reactivation UI
    inactive_isolates = []
    for iso in mlva_config.investigation.isolates.filter(active=False).order_by('name'):
        inactive_isolates.append({
            'id': iso.id,
            'name': iso.name,
        })
    
    return JsonResponse({
        'nodes': nodes,
        'links': links,
        'inactive_isolates': inactive_isolates,
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
def toggle_isolate_active(request, isolate_id):
    """Toggle the active status of an isolate.
    
    This endpoint allows users to inactivate isolates so they don't appear in
    distance matrices, tree views, or affect color scale calculations.
    Returns the updated profiles table HTML for HTMX to replace.
    """
    try:
        isolate = Isolate.objects.get(id=isolate_id)
        
        # Check if user has permission to edit this investigation
        investigation = isolate.investigation
        user_access = UserInvestigation.objects.filter(
            user=request.user,
            investigation=investigation
        ).first()
        
        if not user_access:
            return JsonResponse({'error': 'You do not have access to this investigation'}, status=403)
        
        if not user_access.can_edit():
            return JsonResponse({'error': 'You do not have permission to edit this investigation'}, status=403)
        
        # Parse the request data (handle both JSON and form-encoded)
        try:
            if request.body and request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)
        
        active_str = data.get('active', 'true').lower()
        active = active_str == 'true'
        
        isolate.active = active
        isolate.save()
        
        # Get the MLVAConfig 
        mlva_config = investigation.mlva_configs.first()
        if not mlva_config:
            return JsonResponse({'error': 'MLVA config not found'}, status=404)
        
        color_scheme = request.user.mlva_profile.get_color_scheme()
        
        # Check which tab to return
        tab = data.get('tab', 'profiles')
        
        if tab == 'pairwise':
            # Return the pairwise matrix partial
            pairwise_raw = mlva_config.calculate_pairwise_distances()
            
            # Get profiles table to access inactive rows
            profiles_table = mlva_config.profiles_table(color_scheme=color_scheme)
            
            # Separate active and inactive isolates
            active_isolate_ids = [iso.id for iso in mlva_config.investigation.isolates.filter(active=True).order_by('name')]
            inactive_isolate_ids = [iso.id for iso in mlva_config.investigation.isolates.filter(active=False).order_by('name')]
            
            # Build column headers (only for active isolates)
            column_headers = [
                {'id': iso_id, 'name': pairwise_raw['isolate_names'][iso_id]}
                for iso_id in active_isolate_ids
            ]
            
            # Calculate min and max distances (only between active isolates)
            all_distances = []
            for iso_id_row in active_isolate_ids:
                for iso_id_col in active_isolate_ids:
                    if iso_id_row != iso_id_col:
                        key = tuple(sorted([iso_id_row, iso_id_col]))
                        if key in pairwise_raw['distances']:
                            all_distances.append(pairwise_raw['distances'][key]['strd'])
            
            min_distance = min(all_distances) if all_distances else 0
            max_distance = max(all_distances) if all_distances else 0
            
            # Build active isolates matrix
            pairwise_matrix = []
            for iso_id_row in active_isolate_ids:
                row = {'isolate_id': iso_id_row, 'isolate_name': pairwise_raw['isolate_names'][iso_id_row], 'distances': []}
                for iso_id_col in active_isolate_ids:
                    if iso_id_row == iso_id_col:
                        row['distances'].append({
                            'value': '—',
                            'marked': False,
                            'color_class': 'bg-base-200',
                            'text_class': 'text-base-content/40',
                            'is_string': True
                        })
                    else:
                        key = tuple(sorted([iso_id_row, iso_id_col]))
                        if key in pairwise_raw['distances']:
                            dist_data = pairwise_raw['distances'][key]
                            dist_value = dist_data['strd']
                            bg_color = get_color_for_value(dist_value, min_distance, max_distance, color_scheme)
                            row['distances'].append({
                                'value': dist_value,
                                'marked': dist_data['marked'],
                                'bg_color': bg_color,
                                'text_class': 'text-base-content'
                            })
                        else:
                            row['distances'].append({
                                'value': 'N/A',
                                'marked': False,
                                'color_class': 'bg-base-200',
                                'text_class': 'text-base-content/30',
                                'is_string': True
                            })
                pairwise_matrix.append(row)
            
            # Build inactive isolates matrix
            pairwise_inactive_rows = profiles_table.get('inactive_rows', [])
            
            # Extract sort parameters (use defaults if not provided)
            sort_by = data.get('sort_by', 'isolate') if isinstance(data.get('sort_by'), str) else 'isolate'
            sort_order = data.get('sort_order', 'asc') if isinstance(data.get('sort_order'), str) else 'asc'
            
            allowed_sorts = ['isolate', 'sampling_location', 'sampling_date', 'assembly_size', 'contig_count', 'n50', 'closest_strd', 'total_repeats']
            if sort_by not in allowed_sorts:
                sort_by = 'isolate'
            if sort_order not in ['asc', 'desc']:
                sort_order = 'asc'
            
            # Apply sorting to inactive rows
            pairwise_inactive_rows = _sort_profiles_rows(pairwise_inactive_rows, sort_by, sort_order)
            
            # Build next_sort_orders for template
            next_sort_orders = {
                col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
                for col in allowed_sorts
            }
            
            html = render_to_string(
                'partials/mlva_pairwise_matrix_tab.html',
                {
                    'pairwise_data': {
                        'column_headers': column_headers,
                        'loci': pairwise_raw['loci'],
                        'matrix': pairwise_matrix,
                        'inactive_rows': pairwise_inactive_rows,
                        'headers': profiles_table.get('headers', []),
                        'min_distance': min_distance,
                        'max_distance': max_distance,
                        'gradient': get_gradient_css(color_scheme),
                        'mlva_config_id': mlva_config.id,
                        'investigation_id': investigation.id,
                        'sort_by': sort_by,
                        'sort_order': sort_order,
                        'next_sort_orders': next_sort_orders,
                    }
                },
                request=request
            )
            return HttpResponse(html)
        else:
            # Return profiles table partial (default)
            profiles_table = mlva_config.profiles_table(color_scheme=color_scheme)
            profiles_gradient = get_gradient_css(color_scheme)
            
            # Extract sort parameters (use defaults if not provided)
            sort_by = data.get('sort_by', 'isolate') if isinstance(data.get('sort_by'), str) else 'isolate'
            sort_order = data.get('sort_order', 'asc') if isinstance(data.get('sort_order'), str) else 'asc'
            
            allowed_sorts = ['isolate', 'sampling_location', 'sampling_date', 'assembly_size', 'contig_count', 'n50', 'closest_strd', 'total_repeats']
            if sort_by not in allowed_sorts:
                sort_by = 'isolate'
            if sort_order not in ['asc', 'desc']:
                sort_order = 'asc'
            
            # Apply sorting to profiles table
            profiles_table['active_rows'] = _sort_profiles_rows(profiles_table['active_rows'], sort_by, sort_order)
            profiles_table['inactive_rows'] = _sort_profiles_rows(profiles_table['inactive_rows'], sort_by, sort_order)
            
            # Build next_sort_orders for template
            next_sort_orders = {
                col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
                for col in allowed_sorts
            }
            
            html = render_to_string(
                'partials/mlva_profiles_tab.html',
                {
                    'profiles_table': profiles_table,
                    'profiles_gradient': profiles_gradient,
                    'investigation_id': investigation.id,
                    'mlva_config_id': mlva_config.id,
                    'sort_by': sort_by,
                    'sort_order': sort_order,
                    'next_sort_orders': next_sort_orders,
                },
                request=request
            )
            return HttpResponse(html)
    except Isolate.DoesNotExist:
        return JsonResponse({'error': 'Isolate not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
@require_http_methods(["POST"])
def toggle_all_isolates_active(request, mlva_config_id):
    """Toggle all isolates to active or inactive status.
    
    Receives 'active' boolean to set all isolates to that status.
    Returns the updated profiles table HTML for HTMX to replace.
    """
    try:
        mlva_config = MLVAConfig.objects.get(id=mlva_config_id)
        investigation = mlva_config.investigation
        
        # Check if user has permission to edit this investigation
        user_access = UserInvestigation.objects.filter(
            user=request.user,
            investigation=investigation
        ).first()
        
        if not user_access:
            return JsonResponse({'error': 'You do not have access to this investigation'}, status=403)
        
        if not user_access.can_edit():
            return JsonResponse({'error': 'You do not have permission to edit this investigation'}, status=403)
        
        # Parse the request data
        try:
            if request.body and request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)
        
        active_str = data.get('active', 'true').lower()
        active = active_str == 'true'
        
        # Update all isolates for this investigation
        investigation.isolates.all().update(active=active)
        
        color_scheme = request.user.mlva_profile.get_color_scheme()
        
        # Check which tab to return
        tab = data.get('tab', 'profiles')
        
        if tab == 'pairwise':
            # Return the pairwise matrix partial
            pairwise_raw = mlva_config.calculate_pairwise_distances()
            
            # Get profiles table to access inactive rows
            profiles_table = mlva_config.profiles_table(color_scheme=color_scheme)
            
            # Separate active and inactive isolates
            active_isolate_ids = [iso.id for iso in mlva_config.investigation.isolates.filter(active=True).order_by('name')]
            
            # Build column headers (only for active isolates)
            column_headers = [
                {'id': iso_id, 'name': pairwise_raw['isolate_names'][iso_id]}
                for iso_id in active_isolate_ids
            ]
            
            # Calculate min and max distances (only between active isolates)
            all_distances = []
            for iso_id_row in active_isolate_ids:
                for iso_id_col in active_isolate_ids:
                    if iso_id_row != iso_id_col:
                        key = tuple(sorted([iso_id_row, iso_id_col]))
                        if key in pairwise_raw['distances']:
                            all_distances.append(pairwise_raw['distances'][key]['strd'])
            
            min_distance = min(all_distances) if all_distances else 0
            max_distance = max(all_distances) if all_distances else 0
            
            # Build active isolates matrix
            pairwise_matrix = []
            for iso_id_row in active_isolate_ids:
                row = {'isolate_id': iso_id_row, 'isolate_name': pairwise_raw['isolate_names'][iso_id_row], 'distances': []}
                for iso_id_col in active_isolate_ids:
                    if iso_id_row == iso_id_col:
                        row['distances'].append({
                            'value': '—',
                            'marked': False,
                            'color_class': 'bg-base-200',
                            'text_class': 'text-base-content/40',
                            'is_string': True
                        })
                    else:
                        key = tuple(sorted([iso_id_row, iso_id_col]))
                        if key in pairwise_raw['distances']:
                            dist_data = pairwise_raw['distances'][key]
                            dist_value = dist_data['strd']
                            bg_color = get_color_for_value(dist_value, min_distance, max_distance, color_scheme)
                            row['distances'].append({
                                'value': dist_value,
                                'marked': dist_data['marked'],
                                'bg_color': bg_color,
                                'text_class': 'text-base-content'
                            })
                        else:
                            row['distances'].append({
                                'value': 'N/A',
                                'marked': False,
                                'color_class': 'bg-base-200',
                                'text_class': 'text-base-content/30',
                                'is_string': True
                            })
                pairwise_matrix.append(row)
            
            # Build inactive isolates matrix
            pairwise_inactive_rows = profiles_table.get('inactive_rows', [])
            
            # Extract sort parameters (use defaults if not provided)
            sort_by = data.get('sort_by', 'isolate') if isinstance(data.get('sort_by'), str) else 'isolate'
            sort_order = data.get('sort_order', 'asc') if isinstance(data.get('sort_order'), str) else 'asc'
            
            allowed_sorts = ['isolate', 'sampling_location', 'sampling_date', 'assembly_size', 'contig_count', 'n50', 'closest_strd', 'total_repeats']
            if sort_by not in allowed_sorts:
                sort_by = 'isolate'
            if sort_order not in ['asc', 'desc']:
                sort_order = 'asc'
            
            # Apply sorting to inactive rows
            pairwise_inactive_rows = _sort_profiles_rows(pairwise_inactive_rows, sort_by, sort_order)
            
            # Build next_sort_orders for template
            next_sort_orders = {
                col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
                for col in allowed_sorts
            }
            
            html = render_to_string(
                'partials/mlva_pairwise_matrix_tab.html',
                {
                    'pairwise_data': {
                        'column_headers': column_headers,
                        'loci': pairwise_raw['loci'],
                        'matrix': pairwise_matrix,
                        'inactive_rows': pairwise_inactive_rows,
                        'headers': profiles_table.get('headers', []),
                        'min_distance': min_distance,
                        'max_distance': max_distance,
                        'gradient': get_gradient_css(color_scheme),
                        'mlva_config_id': mlva_config.id,
                        'investigation_id': investigation.id,
                        'sort_by': sort_by,
                        'sort_order': sort_order,
                        'next_sort_orders': next_sort_orders,
                    }
                },
                request=request
            )
            return HttpResponse(html)
        else:
            # Return profiles table partial (default)
            profiles_table = mlva_config.profiles_table(color_scheme=color_scheme)
            profiles_gradient = get_gradient_css(color_scheme)
            
            # Extract sort parameters (use defaults if not provided)
            sort_by = data.get('sort_by', 'isolate') if isinstance(data.get('sort_by'), str) else 'isolate'
            sort_order = data.get('sort_order', 'asc') if isinstance(data.get('sort_order'), str) else 'asc'
            
            allowed_sorts = ['isolate', 'sampling_location', 'sampling_date', 'assembly_size', 'contig_count', 'n50', 'closest_strd', 'total_repeats']
            if sort_by not in allowed_sorts:
                sort_by = 'isolate'
            if sort_order not in ['asc', 'desc']:
                sort_order = 'asc'
            
            # Apply sorting to profiles table
            profiles_table['active_rows'] = _sort_profiles_rows(profiles_table['active_rows'], sort_by, sort_order)
            profiles_table['inactive_rows'] = _sort_profiles_rows(profiles_table['inactive_rows'], sort_by, sort_order)
            
            # Build next_sort_orders for template
            next_sort_orders = {
                col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
                for col in allowed_sorts
            }
            
            html = render_to_string(
                'partials/mlva_profiles_tab.html',
                {
                    'profiles_table': profiles_table,
                    'profiles_gradient': profiles_gradient,
                    'investigation_id': investigation.id,
                    'mlva_config_id': mlva_config.id,
                    'sort_by': sort_by,
                    'sort_order': sort_order,
                    'next_sort_orders': next_sort_orders,
                },
                request=request
            )
            return HttpResponse(html)
    except MLVAConfig.DoesNotExist:
        return JsonResponse({'error': 'MLVA config not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

