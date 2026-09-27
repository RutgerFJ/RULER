from django import forms
from core.models import MLVAUser


class UserSettingsForm(forms.Form):
    """
    Form for user settings including color scheme preferences.
    """
    COLOR_SCHEME_CHOICES = [
        ('default', 'Blue to Red'),
        ('viridis', 'Viridis'),
        ('plasma', 'Plasma'),
        ('cool', 'Cool'),
        ('warm', 'Warm'),
        ('grayscale', 'Grayscale'),
    ]
    
    color_scheme = forms.ChoiceField(
        choices=COLOR_SCHEME_CHOICES,
        widget=forms.RadioSelect()
    )
    
    def __init__(self, mlva_user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mlva_user = mlva_user
        
        # Initialize form with current values
        if not args:  # GET request
            self.fields['color_scheme'].initial = mlva_user.get_color_scheme()
    
    def save(self):
        """Save settings to MLVAUser"""
        if self.mlva_user.settings is None:
            self.mlva_user.settings = {}
        self.mlva_user.settings['color_scheme'] = self.cleaned_data['color_scheme']
        
        self.mlva_user.save()
        return self.mlva_user
