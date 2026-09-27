from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from core.forms.locus import LocusSetCreationForm
from core.models import LocusSet, Locus


@login_required(login_url='login')
def locus_sets(request):
    """
    List all available locus sets with filtering and sorting.
    Supports searching by name and description, and sorting by various fields.
    """
    # 1. Extract and clean request parameters
    get_param = lambda key: request.GET.get(key, '').strip()
    
    filters = {
        'search_name': get_param('search_name'),
        'search_locus_name': get_param('search_locus_name'),
        'filter_loci_count': get_param('filter_loci_count'),
    }
    
    sort_by = request.GET.get('sort_by', 'name')
    sort_order = request.GET.get('sort_order', 'asc')
    
    # 2. Fetch and process the dataset
    locus_sets_list = (
        LocusSet.objects
        .annotate_counts()
        .apply_filters(filters)
        .apply_sorting(sort_by, sort_order)
    )
    
    # 3. Build UI state (e.g., next sort directions)
    allowed_sorts = ['name', 'loci_count']
    if sort_by not in allowed_sorts:
        sort_by = 'name'
        
    next_sort_orders = {
        col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
        for col in allowed_sorts
    }
    
    # 4. Render response
    context = {
        'locus_sets': locus_sets_list,
        'sort_by': sort_by,
        'sort_order': sort_order,
        'next_sort_orders': next_sort_orders,
        **filters,  # Unpacks search_name, search_locus_name, filter_loci_count into context
    }
    
    template = 'partials/locus_sets_table.html' if request.headers.get('HX-Request') else 'locus_sets.html'
    return render(request, template, context)


@login_required(login_url='login')
def locus_set_detail(request, locus_set_id):
    """
    Display details of a specific locus set and its loci.
    Supports sorting by name and repeat_length (calculated field).
    """
    locus_set = get_object_or_404(LocusSet, id=locus_set_id)
    
    # Handle sorting parameters
    sort_by = request.GET.get('sort_by', 'name')
    sort_order = request.GET.get('sort_order', 'asc')
    
    # Get loci
    loci = list(locus_set.loci.all())
    
    allowed_sorts = ['name', 'repeat_length']
    if sort_by not in allowed_sorts:
        sort_by = 'name'
    
    # Apply sorting (use reverse=True for desc since repeat_length is calculated)
    reverse = sort_order == 'desc'
    if sort_by == 'repeat_length':
        loci.sort(key=lambda x: x.repeat_length(), reverse=reverse)
    else:  # sort by name
        loci.sort(key=lambda x: x.name.lower(), reverse=reverse)
    
    # Build UI state (next sort directions for toggle behavior)
    next_sort_orders = {
        col: ('asc' if sort_order == 'desc' and col == sort_by else 'desc' if col == sort_by else 'asc')
        for col in allowed_sorts
    }
    
    context = {
        'locus_set': locus_set,
        'loci': loci,
        'loci_count': len(loci),
        'sort_by': sort_by,
        'sort_order': sort_order,
        'next_sort_orders': next_sort_orders,
    }
    
    template = 'partials/loci_table.html' if request.headers.get('HX-Request') else 'locus_sets_detail.html'
    return render(request, template, context)


@login_required(login_url='login')
@require_http_methods(["GET", "POST"])
def create_locus_set(request, step=1):
    """
    Multi-step wizard for creating a new locus set.
    
    Steps:
    1. Enter locus set name and description
    2. Upload primers FASTA file
    3. Review and confirm
    4. Completion
    """
    
    # Reset wizard if accessing step 1 via GET (starting fresh)
    if request.method == 'GET' and step == 1:
        request.session['locus_set_creation'] = {
            'data': {},
            'max_completed_step': 0,
        }
        request.session.modified = True
    
    # Initialize session storage
    if 'locus_set_creation' not in request.session:
        request.session['locus_set_creation'] = {
            'data': {},
            'max_completed_step': 0,
        }
    
    session_data = request.session['locus_set_creation']
    
    # Prevent invalid steps
    if step < 1:
        return redirect('create_locus_set', step=1)
    
    if step > 4:
        return redirect('create_locus_set', step=4)
    
    # Prevent skipping ahead
    max_completed = session_data.get('max_completed_step', 0)
    if step > max_completed + 1:
        return redirect('create_locus_set', step=max_completed + 1)
    
    if request.method == 'POST':
        # Handle explicit back action
        if request.POST.get('action') == 'back':
            return redirect('create_locus_set', step=max(step - 1, 1))
        
        return _process_locus_set_creation_step(request, step, session_data)
    
    return _render_locus_set_creation_step(request, step)


def _render_locus_set_creation_step(request, step):
    """Render the appropriate form for the current step."""
    session_data = request.session.get('locus_set_creation', {})
    data = session_data.get('data', {})
    
    context = {
        'step': step,
        'total_steps': 4,
    }
    
    # Calculate progress bar percentage
    progress_percent_map = {1: 12.5, 2: 37.5, 3: 62.5, 4: 100}
    context['progress_percent'] = progress_percent_map.get(step, 0)
    
    if step == 1:
        form = LocusSetCreationForm(initial={
            'name': data.get('name', ''),
            'description': data.get('description', ''),
        })
        context['form'] = form
        context['step_title'] = 'Locus Set Details'
        
    elif step == 2:
        form = LocusSetCreationForm(initial={
            'name': data.get('name', ''),
            'description': data.get('description', ''),
        })
        context['form'] = form
        context['step_title'] = 'Upload Primers'
        context['locus_set_name'] = data.get('name', '')
        context['locus_set_description'] = data.get('description', '')
        context['loci'] = data.get('locus_names', [])
        
    elif step == 3:
        context['step_title'] = 'Review & Confirm'
        context['locus_set_name'] = data.get('name', '')
        context['locus_set_description'] = data.get('description', '')
        context['locus_count'] = data.get('locus_count', 0)
        context['loci'] = data.get('locus_names', [])
        
    elif step == 4:
        context['step_title'] = 'Complete'
        context['locus_set_id'] = data.get('locus_set_id')
        context['locus_set_name'] = data.get('name', '')
        context['locus_count'] = data.get('locus_count', 0)
        context['loci'] = data.get('locus_names', [])
    
    return render(request, 'create_locus_set.html', context)


def _process_locus_set_creation_step(request, step, session_data):
    """Process form submission for current step."""
    data = session_data.get('data', {})
    
    if step == 1:
        return _process_locus_set_step_1_details(request, session_data, data)
    elif step == 2:
        return _process_locus_set_step_2_primers(request, session_data, data)
    elif step == 3:
        return _process_locus_set_step_3_confirmation(request, session_data, data)
    elif step == 4:
        return _process_locus_set_step_4_finalization(request, session_data, data)


def _process_locus_set_step_1_details(request, session_data, data):
    """Process step 1: Locus set name and description."""
    # Don't use form validation on step 1, do manual validation instead
    errors = {}
    
    if request.POST:
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        
        # Validate name
        if not name:
            errors['name'] = 'Locus set name cannot be empty.'
        elif len(name) > 20:
            errors['name'] = 'Name must be 20 characters or less.'
        elif LocusSet.objects.filter(name=name).exists():
            errors['name'] = f'A locus set named "{name}" already exists.'
        
        # Validate description
        if not description:
            errors['description'] = 'Description cannot be empty.'
        elif len(description) > 200:
            errors['description'] = 'Description must be 200 characters or less.'
        
        # If no errors, proceed
        if not errors:
            data['name'] = name
            data['description'] = description
            session_data['data'] = data
            session_data['max_completed_step'] = 1
            request.session['locus_set_creation'] = session_data
            request.session.modified = True
            return redirect('create_locus_set', step=2)
    
    # Re-render with errors - create form for display only
    form = LocusSetCreationForm(initial={
        'name': data.get('name', ''),
        'description': data.get('description', ''),
    })
    # Add manual errors to the form for display
    for field, error in errors.items():
        form.add_error(field, error)
    
    context = {
        'step': 1,
        'total_steps': 4,
        'progress_percent': 25,
        'form': form,
        'step_title': 'Locus Set Details',
    }
    return render(request, 'create_locus_set.html', context)


def _process_locus_set_step_2_primers(request, session_data, data):
    """Process step 2: Upload primer FASTA file."""
    form = LocusSetCreationForm(request.POST, request.FILES)
    
    if not form.is_valid():
        # Re-render with errors
        context = {
            'step': 2,
            'total_steps': 4,
            'progress_percent': 50,
            'form': form,
            'step_title': 'Upload Primers',
            'locus_set_name': data.get('name', ''),
        }
        return render(request, 'create_locus_set.html', context)
    
    try:
        if not hasattr(form, 'parsed_primers'):
            raise ValueError("Form did not parse primer file correctly.")
        
        parsed_primers = form.parsed_primers
        
        # Store in session
        data['parsed_primers'] = parsed_primers
        data['locus_count'] = len(parsed_primers)
        data['locus_names'] = sorted(list(parsed_primers.keys()))
        session_data['data'] = data
        session_data['max_completed_step'] = 2
        request.session['locus_set_creation'] = session_data
        request.session.modified = True
        
        return redirect('create_locus_set', step=3)
    
    except Exception as e:
        messages.error(request, f"Error processing primer file: {str(e)}")
        context = {
            'step': 2,
            'total_steps': 4,
            'progress_percent': 50,
            'form': form,
            'step_title': 'Upload Primers',
            'locus_set_name': data.get('name', ''),
        }
        return render(request, 'create_locus_set.html', context)


def _process_locus_set_step_3_confirmation(request, session_data, data):
    """Process step 3: Review and confirmation."""
    try:
        # Create the LocusSet and Locus objects
        locus_set_name = data.get('name')
        locus_set_description = data.get('description')
        parsed_primers = data.get('parsed_primers', {})
        
        locus_set = LocusSet.objects.create(
            name=locus_set_name,
            description=locus_set_description
        )
        
        # Create Locus objects
        for locus_name, locus_data in parsed_primers.items():
            Locus.objects.create(
                locus_set=locus_set,
                name=locus_name,
                forward_primer=locus_data['forward'],
                reverse_primer=locus_data['reverse'],
                repeat_sequence=locus_data['repeat']
            )
        
        data['locus_set_id'] = locus_set.id
        session_data['data'] = data
        session_data['max_completed_step'] = 3
        request.session['locus_set_creation'] = session_data
        request.session.modified = True
        
        return redirect('create_locus_set', step=4)
    
    except Exception as e:
        messages.error(request, f"Error creating locus set: {str(e)}")
        context = {
            'step': 3,
            'total_steps': 4,
            'progress_percent': 75,
            'step_title': 'Review & Confirm',
            'locus_set_name': data.get('name', ''),
            'locus_set_description': data.get('description', ''),
            'locus_count': data.get('locus_count', 0),
        }
        return render(request, 'create_locus_set.html', context)


def _process_locus_set_step_4_finalization(request, session_data, data):
    """Process step 4: Finalization - handle completion actions."""
    
    # Handle user actions on completion page
    if request.POST.get('create_another'):
        # User wants to create another locus set - clear session and redirect to step 1
        if 'locus_set_creation' in request.session:
            del request.session['locus_set_creation']
            request.session.modified = True
        return redirect('create_locus_set', step=1)
    
    if request.POST.get('view_all'):
        # User wants to see all locus sets - clear session and redirect
        if 'locus_set_creation' in request.session:
            del request.session['locus_set_creation']
            request.session.modified = True
        return redirect('locus_sets')
    
    # Default: redirect to locus sets view
    if 'locus_set_creation' in request.session:
        del request.session['locus_set_creation']
        request.session.modified = True
    
    return redirect('locus_sets')
