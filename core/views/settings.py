from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from core.forms.settings import UserSettingsForm
from core.color_schemes import COLOR_SCHEMES, get_gradient_css


@login_required(login_url='login')
@require_http_methods(["GET", "POST"])
def settings_view(request):
    """
    Display and handle user settings including color scheme preferences.
    """
    mlva_user = request.user.mlva_profile
    
    if request.method == 'POST':
        form = UserSettingsForm(mlva_user, request.POST)
        if form.is_valid():
            form.save()
            return redirect('settings')
    else:
        form = UserSettingsForm(mlva_user)
    
    # Prepare color scheme options with their metadata and gradients
    color_scheme_options = []
    for key, scheme_data in COLOR_SCHEMES.items():
        color_scheme_options.append({
            'key': key,
            'name': scheme_data['name'],
            'description': scheme_data['description'],
            'gradient': get_gradient_css(key),
            'selected': key == mlva_user.get_color_scheme(),
        })
    
    context = {
        'form': form,
        'color_scheme_options': color_scheme_options,
        'current_color_scheme': mlva_user.get_color_scheme(),
    }
    
    return render(request, 'settings.html', context)
