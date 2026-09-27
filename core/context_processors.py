def active_page(request):
    """
    Context processor to determine the active navigation page.
    Returns the active section name based on the current URL path.
    """
    path = request.path.lower()
    
    # Determine active section based on URL path
    if path.startswith('/locus-sets') or path.startswith('/locus_sets'):
        active_section = 'locus_sets'
    elif path.startswith('/investigations'):
        active_section = 'investigations'
    elif path.startswith('/settings'):
        active_section = 'settings'
    else:
        # Default to investigations for root path
        active_section = 'investigations'
    
    return {
        'active_section': active_section,
    }
