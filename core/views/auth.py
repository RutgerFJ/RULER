from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.views.decorators.http import require_http_methods
from django.shortcuts import render, redirect

from core.forms.auth import SignUpForm, LoginForm


@require_http_methods(["GET", "POST"])
def signup_view(request):
    """
    Handle user registration. Creates both User and MLVAUser instances.
    """
    if request.user.is_authenticated:
        return redirect('investigations')
    
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                # User is automatically logged in after signup
                login(request, user)
            return redirect('investigations')
    else:
        form = SignUpForm()
    
    return render(request, 'signup.html', {'form': form})


@require_http_methods(["GET", "POST"])
def login_view(request):
    """
    Handle user login.
    """
    if request.user.is_authenticated:
        return redirect('investigations')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.GET.get('next', 'investigations')
            return redirect(next_url)
    else:
        form = LoginForm()
    
    return render(request, 'login.html', {'form': form})


@login_required(login_url='login')
def logout_view(request):
    """
    Handle user logout.
    """
    logout(request)
    return redirect('login')
