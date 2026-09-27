from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.db.models import Avg, Count, Q, StdDev
from django.contrib import messages

from core.forms import AssemblyUploadForm, BasicInvestigationForm, MetadataUploadForm
from core.models import Investigation, UserInvestigation, Isolate, Contig

@login_required(login_url='login')
@require_http_methods(["GET", "POST"])
def create_investigation(request, step=1):
    """
    Multi-step investigation wizard.
    """

    # Initialize session storage
    if 'investigation_creation' not in request.session:
        request.session['investigation_creation'] = {
            'data': {},
            'temp_zip_path': None,
            'max_completed_step': 0,
        }

    session_data = request.session['investigation_creation']

    # Prevent invalid steps
    if step < 1:
        return redirect('create_investigation', step=1)

    if step > 5:
        return redirect('create_investigation', step=5)

    # Prevent skipping ahead
    max_completed = session_data.get('max_completed_step', 0)


    if step > max_completed + 1:
        return redirect(
            'create_investigation',
            step=max_completed + 1
        )

    if request.method == 'POST':

        # Handle explicit back action
        if request.POST.get('action') == 'back':
            return redirect(
                'create_investigation',
                step=max(step - 1, 1)
            )

        return _process_investigation_creation_step(
            request,
            step
        )

    return _render_investigation_creation_step(
        request,
        step
    )


def _render_investigation_creation_step(request, step):
    """Render the appropriate form for the current step."""
    session_data = request.session['investigation_creation']
    data = session_data.get('data', {})
    
    context = {
        'step': step,
        'total_steps': 5,
    }
    
    # Calculate progress bar percentage (stops before current step, full at step 5)
    progress_percent_map = {1: 10, 2: 30, 3: 50, 4: 70, 5: 100}
    context['progress_percent'] = progress_percent_map.get(step, 0)
    
    if step == 1:
        form = BasicInvestigationForm(initial={
            'name': data.get('name', ''),
            'description': data.get('description', ''),
        })
        context['form'] = form
        context['step_title'] = 'Investigation Details'
        
    elif step == 2:
        form = AssemblyUploadForm()
        context['form'] = form
        context['step_title'] = 'Upload Assemblies'
        context['investigation_name'] = data.get('name', '')
        # Pass assembly warning if it exists
        if data.get('assembly_warning'):
            context['assembly_warning'] = data.get('assembly_warning')
        
    elif step == 3:
        form = MetadataUploadForm()
        context['form'] = form
        context['step_title'] = 'Upload Metadata'
        context['investigation_name'] = data.get('name', '')
        context['assembly_count'] = len(data.get('isolate_names', []))
        context['isolate_names'] = data.get('isolate_names', [])
        # Pass metadata warning and unmatched isolates if they exist
        if data.get('metadata_warning'):
            context['metadata_warning'] = data.get('metadata_warning')
        if data.get('unmatched_metadata_isolates'):
            context['unmatched_metadata_isolates'] = data.get('unmatched_metadata_isolates')
        
    elif step == 4:
        context['step_title'] = 'Review & Confirm'
        context['investigation_name'] = data.get('name', '')
        context['investigation_description'] = data.get('description', '')
        context['isolate_count'] = len(data.get('isolate_names', []))
        context['isolates'] = data.get('isolate_metadata', [])
        
    elif step == 5:
        # Finalization step - show completion
        context['investigation_id'] = data.get('investigation_id')
        context['investigation_name'] = data.get('name', '')
        context['isolate_count'] = len(data.get('isolate_names', []))
        context['mlva_config_id'] = data.get('mlva_config_id')
    
    return render(request, 'create_investigation.html', context)


def _process_investigation_creation_step(request, step):
    """Process form submission for current step."""
    session_data = request.session['investigation_creation']
    data = session_data.get('data', {})
    
    if step == 1:
        return _process_step_1_basic_info(request, session_data, data)
    elif step == 2:
        return _process_step_2_assemblies(request, session_data, data)
    elif step == 3:
        return _process_step_3_metadata(request, session_data, data)
    elif step == 4:
        return _process_step_4_confirmation(request, session_data, data)
    elif step == 5:
        return _process_step_5_finalization(request, session_data, data)


def _process_step_1_basic_info(request, session_data, data):
    """Process step 1: Basic investigation information."""
    form = BasicInvestigationForm(request.POST)
    
    if form.is_valid():
        # Store in session
        data['name'] = form.cleaned_data['name']
        data['description'] = form.cleaned_data['description']
        session_data['data'] = data
        session_data['max_completed_step'] = 1

        request.session['investigation_creation'] = session_data
        request.session.modified = True
        
        return redirect('create_investigation', step=2)
    else:
        # Re-render with errors
        context = {
            'step': 1,
            'total_steps': 5,
            'form': form,
            'step_title': 'Investigation Details',
        }
        return render(request, 'create_investigation.html', context)


def _process_step_2_assemblies(request, session_data, data):
    """Process step 2: Assembly ZIP upload and validation."""
    form = AssemblyUploadForm(request.POST, request.FILES)
    
    if form.is_valid():
        # Extract FASTA data from form
        fasta_data = form.fasta_data  # {isolate_name: [(header, seq), ...]}
        isolate_names = list(fasta_data.keys())
        
        # Store in session
        data['isolate_names'] = isolate_names
        data['fasta_data'] = fasta_data
        # Store warning message if non-FASTA files were found
        if hasattr(form, 'non_fasta_warning') and form.non_fasta_warning:
            data['assembly_warning'] = form.non_fasta_warning
        
        session_data['data'] = data
        session_data['max_completed_step'] = 2

        request.session['investigation_creation'] = session_data
        request.session.modified = True
        
        return redirect('create_investigation', step=3)
    else:
        # Re-render with errors
        context = {
            'step': 2,
            'total_steps': 5,
            'form': form,
            'step_title': 'Upload Assemblies',
            'investigation_name': data.get('name', ''),
        }
        return render(request, 'create_investigation.html', context)


def _process_step_3_metadata(request, session_data, data):
    """Process step 3: Metadata CSV upload and validation."""
    form = MetadataUploadForm(request.POST, request.FILES)
    
    # Check if we have existing metadata from a previous upload
    # (user might be just acknowledging unmatched isolates)
    existing_metadata = data.get('metadata')
    has_new_file = bool(request.FILES.get('metadata_csv'))
    
    # Always validate the form to get cleaned_data (including acknowledge_unmatched checkbox)
    form_valid = form.is_valid()
    
    # Determine which metadata to use
    if has_new_file and form_valid:
        # New file provided and validated
        metadata = form.metadata
    elif not has_new_file and existing_metadata:
        # No new file, using existing metadata
        # Form is still "valid" in the sense we should accept the submission
        # if the checkbox is checked
        metadata = existing_metadata
    else:
        # No valid metadata available
        if not has_new_file and not existing_metadata:
            form.add_error('metadata_csv', 'Please upload a metadata CSV file.')
        
        context = {
            'step': 3,
            'total_steps': 5,
            'progress_percent': 50,
            'form': form,
            'step_title': 'Upload Metadata',
            'investigation_name': data.get('name', ''),
            'assembly_count': len(data.get('isolate_names', [])),
            'isolate_names': data.get('isolate_names', []),
        }
        return render(request, 'create_investigation.html', context)
    
    # Validate isolate name matching
    assembly_isolates = set(data.get('isolate_names', []))
    metadata_isolates = set(metadata.keys())
    
    # Check: all assemblies MUST have metadata
    missing_from_metadata = assembly_isolates - metadata_isolates
    
    if missing_from_metadata:
        form.add_error(None, _format_isolate_mismatch_error(
            missing_from_metadata, set()
        ))
        
        context = {
            'step': 3,
            'total_steps': 5,
            'progress_percent': 50,
            'form': form,
            'step_title': 'Upload Metadata',
            'investigation_name': data.get('name', ''),
            'assembly_count': len(assembly_isolates),
            'isolate_names': sorted(assembly_isolates),
        }
        return render(request, 'create_investigation.html', context)
    
    # Check: warn about metadata without matching assemblies
    extra_in_metadata = metadata_isolates - assembly_isolates
    if extra_in_metadata:
        # User MUST acknowledge unmatched isolates with checkbox
        if not form.cleaned_data.get('acknowledge_unmatched'):
            form.add_error('acknowledge_unmatched', 
                'You must acknowledge the unmatched metadata isolates to continue.'
            )
            
            # Store metadata in data so it persists for next submission
            data['metadata'] = metadata
            data['unmatched_metadata_isolates'] = sorted(extra_in_metadata)

            session_data['data'] = data
            session_data['max_completed_step'] = 3

            request.session['investigation_creation'] = session_data
            request.session.modified = True
            
            context = {
                'step': 3,
                'total_steps': 5,
                'progress_percent': 50,
                'form': form,
                'step_title': 'Upload Metadata',
                'investigation_name': data.get('name', ''),
                'assembly_count': len(assembly_isolates),
                'isolate_names': sorted(assembly_isolates),
                'metadata_warning': (
                    f"The following isolate names in the metadata CSV were not found in the assemblies "
                    f"and will be ignored: {', '.join(sorted(extra_in_metadata)[:5])}"
                    f"{'...' if len(extra_in_metadata) > 5 else ''}. "
                    f"Only metadata matching uploaded assemblies will be used."
                ),
                'unmatched_metadata_isolates': sorted(extra_in_metadata),
            }
            return render(request, 'create_investigation.html', context)
    
    # Store in session (filter metadata to only matched isolates)
    filtered_metadata = {
        name: metadata[name] for name in assembly_isolates if name in metadata
    }
    
    data['metadata'] = filtered_metadata
    data['isolate_metadata'] = [
        {
            'name': name,
            'sampling_date': filtered_metadata[name].get('sampling_date'),
            'sampling_location': filtered_metadata[name].get('sampling_location'),
            'contig_count': len(data['fasta_data'][name]),
        }
        for name in assembly_isolates
    ]
    # Clear warning/unmatched data after successful confirmation
    data['metadata_warning'] = None
    data['unmatched_metadata_isolates'] = []
    
    session_data['data'] = data
    session_data['max_completed_step'] = 3

    request.session['investigation_creation'] = session_data
    request.session.modified = True
    
    return redirect('create_investigation', step=4)


def _process_step_4_confirmation(request, session_data, data):
    """Process step 4: User confirmation - create investigation."""
    session_data['max_completed_step'] = 4
    
    request.session['investigation_creation'] = session_data
    request.session.modified = True
    
    # Perform finalization immediately (create DB records)
    try:
        investigation_id = _finalize_investigation_creation(
            request.user, data
        )
        data['investigation_id'] = investigation_id

        session_data['data'] = data

        request.session['investigation_creation'] = session_data
        request.session.modified = True
        
        return redirect('create_investigation', step=5)
    except Exception as e:
        messages.error(request, f"Error creating investigation: {str(e)}")
        context = {
            'step': 4,
            'total_steps': 5,
            'step_title': 'Review & Confirm',
            'investigation_name': data.get('name', ''),
            'investigation_description': data.get('description', ''),
            'isolate_count': len(data.get('isolate_names', [])),
            'isolates': data.get('isolate_metadata', []),
        }
        return render(request, 'create_investigation.html', context)


def _process_step_5_finalization(request, session_data, data):
    """Process step 5: Finalization complete - show results."""
    
    # Handle user actions on completion page
    if request.POST.get('create_analysis'):
        # User wants to create an analysis - clear session and redirect to MLVAConfig creation
        if 'investigation_creation' in request.session:
            del request.session['investigation_creation']
            request.session.modified = True
        
        # Clear any pending messages to avoid carrying over investigation creation messages
        storage = messages.get_messages(request)
        for message in storage:
            pass  # Iterate through to mark all messages as consumed
        
        return redirect('create_mlvaconfig', investigation_id=data.get('investigation_id'))
    
    if request.POST.get('view_investigation'):
        # User wants to view the investigation - clear session and redirect
        if 'investigation_creation' in request.session:
            del request.session['investigation_creation']
            request.session.modified = True
        return redirect('investigations_detail', investigation_id=data.get('investigation_id'))
    
    if request.POST.get('view_all'):
        # User wants to see all investigations - clear session and redirect
        if 'investigation_creation' in request.session:
            del request.session['investigation_creation']
            request.session.modified = True
        return redirect('investigations')
    
    # Initial GET render of step 5 - show completion page
    context = {
        'step': 5,
        'total_steps': 5,
        'investigation_id': data.get('investigation_id'),
        'investigation_name': data.get('name', ''),
        'isolate_count': len(data.get('isolate_names', [])),
    }
    
    return render(request, 'create_investigation.html', context)


def _finalize_investigation_creation(user, data):
    """
    Create Investigation, Isolates and Contigs.
    Optimized to batch operations and calculate stats efficiently.
    Returns (investigation_id).
    """
    with transaction.atomic():
        # 1. Create Investigation
        investigation = Investigation.objects.create(
            name=data['name'],
            description=data.get('description', ''),
            status='active'
        )
        
        # 2. Create UserInvestigation (link user as owner)
        UserInvestigation.objects.create(
            user=user,
            investigation=investigation,
            role='owner'
        )
        
        # 3. Create Isolates and Contigs
        for isolate_name in data['isolate_names']:
            isolate = Isolate.objects.create(
                investigation=investigation,
                name=isolate_name,
                sampling_date=data['metadata'][isolate_name]['sampling_date'],
                sampling_location=data['metadata'][isolate_name]['sampling_location'],
                notes=data['metadata'][isolate_name].get('notes', '')
            )

            # Get FASTA sequences for this isolate
            sequences = data['fasta_data'][isolate_name]  # [(header, seq), ...]
            
            # Prepare contigs with pre-calculated length and gc_content
            # (required because bulk_create bypasses save() method)
            contigs = []
            for header, sequence in sequences:
                contig = Contig(
                    isolate=isolate,
                    fasta_header=header,
                    sequence=sequence
                )
                # Pre-calculate fields that save() would normally calculate
                contig.length = len(sequence)
                contig.gc_content = contig.calculate_gc_content()
                contigs.append(contig)
            
            # Bulk create all contigs at once (avoids repeated signal triggers)
            Contig.objects.bulk_create(contigs)
            
            # Calculate all stats once after all contigs created
            # This is more efficient than letting signals recalculate for each contig
            isolate.calculate_and_update_stats()
        
        return investigation.id


def _format_isolate_mismatch_error(missing, extra):
    """Format isolate name mismatch error message."""
    message = "Isolate name mismatch between assemblies and metadata:"
    
    if missing:
        message += f"\n• Missing from CSV: {', '.join(sorted(missing)[:5])}"
        if len(missing) > 5:
            message += f" (+{len(missing) - 5} more)"
    
    if extra:
        message += f"\n• Extra in CSV (not in assemblies): {', '.join(sorted(extra)[:5])}"
        if len(extra) > 5:
            message += f" (+{len(extra) - 5} more)"
    
    message += "\nPlease ensure all assembly filenames match the isolate_name column in the CSV."
    
    return message


@login_required(login_url='login')
def investigations(request):
    """
    Main investigations view showing user's investigations.
    Supports filtering, sorting, and displays annotated analysis counts.
    """
    # 1. Extract and clean request parameters
    get_param = lambda key: request.GET.get(key, '').strip()
    
    filters = {
        'search_name': get_param('search_name'),
        'status': get_param('filter_status'),
        'date_from': get_param('filter_date_from'),
        'date_to': get_param('filter_date_to'),
    }
    
    sort_by = request.GET.get('sort_by', 'created_at')
    sort_order = request.GET.get('sort_order', 'desc')
    
    # 2. Fetch and process the dataset
    user_investigations = (
        UserInvestigation.objects.filter(user=request.user)
        .select_related('investigation')
        .annotate_counts()
        .apply_filters(filters)
        .apply_sorting(sort_by, sort_order)
    )
    
    # 3. Build UI state (e.g., next sort directions)
    allowed_sorts = ['name', 'status', 'role', 'created_at', 'analyses']
    if sort_by not in allowed_sorts:
        sort_by = 'created_at'
        
    next_sort_orders = {
        col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
        for col in allowed_sorts
    }
    
    # 4. Render response
    context = {
        'user_investigations': user_investigations,
        'sort_by': sort_by,
        'sort_order': sort_order,
        'next_sort_orders': next_sort_orders,
        **filters,  # Unpacks search_name, status, date_from, date_to into context
    }
    
    template = 'partials/investigations_table.html' if request.headers.get('HX-Request') else 'investigations.html'
    return render(request, template, context)


@login_required(login_url='login')
def investigations_detail(request, investigation_id):
    """
    View details of a specific investigation including isolates and analysis jobs.
    Optimized for performance by using prefetch_related and select_related.
    """
    # Get the investigation and verify user has access
    investigation = get_object_or_404(Investigation, id=investigation_id)
    user = request.user
    user_inv = get_object_or_404(
        UserInvestigation,
        user=user,
        investigation=investigation
    )
    
    # Get isolates with prefetched contigs - avoids N+1 queries
    # Uses stored fields (assembly_size, contig_count, n50) for fast display
    isolates = sorted(list(investigation.isolates.iterator()), reverse=True)
    
    # Get MLVA configs with related locus_set - avoids FK lookup on each row
    mlva_configs = list(investigation.mlva_configs.select_related('locus_set'))

    # Calculate assembly statistics
    stats = Isolate.objects.filter(
        investigation=investigation, 
        active=True
    ).aggregate(
        avg_contigs=Avg('contig_count'),
        std_contigs=StdDev('contig_count'),
        avg_size=Avg('assembly_size'),
        std_size=StdDev('assembly_size'),
        avg_n50=Avg('n50'),
        std_n50=StdDev('n50')
    )
    
    context = {
        'investigation': investigation,
        'stats': stats,
        'user_investigation': user_inv,
        'num_isolates': len(isolates),
        'isolates': isolates,
        'num_mlva_configs': len(mlva_configs),
        'mlva_configs': mlva_configs
    }
    
    return render(request, 'investigations_detail.html', context)


def investigations_isolate_partial(request, investigation_id, isolate_id):
    """
    View details of a specific isolate.
    """
    user = request.user

    # Get the investigation and verify user has access
    investigation = get_object_or_404(Investigation, id=investigation_id)
    
    # Check if user has access to this investigation
    user_inv = get_object_or_404(
        UserInvestigation,
        user=user,
        investigation=investigation
    )

    isolate = get_object_or_404(Isolate, id=isolate_id)

    context = {
        'contigs': list(isolate.contigs.iterator())
    }

    return render(request, 'partials/investigations_isolate.html', context)