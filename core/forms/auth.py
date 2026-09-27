from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm

from core.models import MLVAUser

class SignUpForm(UserCreationForm):
    """
    Extended sign-up form that creates both a User and MLVAUser instance.
    """
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'input input-bordered w-full bg-base-100',
            'placeholder': 'Username'
        })
    )
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'class': 'input input-bordered w-full bg-base-100',
            'placeholder': 'Password'
        })
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'class': 'input input-bordered w-full bg-base-100',
            'placeholder': 'Confirm password'
        })
    )

    class Meta:
        model = User
        fields = ('username', 'password1', 'password2')

    def save(self, commit=True):
        """
        Save user and automatically create MLVAUser instance.
        """
        user = super().save(commit=False)
        
        if commit:
            user.save()
            # Automatically create MLVAUser instance
            MLVAUser.objects.create(
                user=user
            )
        
        return user


class LoginForm(AuthenticationForm):
    """
    Custom login form with Bootstrap styling.
    """
    username = forms.CharField(
        max_length=254,
        widget=forms.TextInput(attrs={
            'class': 'input input-bordered w-full bg-base-100',
            'placeholder': 'Username',
            'autofocus': True
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'input input-bordered w-full bg-base-100',
            'placeholder': 'Password'
        })
    )
