from django.contrib import admin
from django.urls import path

from core.views.auth import *
from core.views.investigation import *
from core.views.locus import *
from core.views.mlva import *
from core.views.settings import *

urlpatterns = [
    path("admin/", admin.site.urls),

    # General views
    path("signup", signup_view, name="signup"),
    path("login", login_view, name="login"),
    path("logout", logout_view, name="logout"),
    path("settings", settings_view, name="settings"),

    # Investigations
    path("", investigations, name="index"),
    path("investigations", investigations, name="investigations"),
    path("investigations/create/<int:step>", create_investigation, name="create_investigation"),
    path("investigations/<int:investigation_id>", investigations_detail, name="investigations_detail"),
    path("investigations/<int:investigation_id>/mlva/create", create_mlvaconfig, name="create_mlvaconfig"),
    path("investigations/<int:investigation_id>/mlva/primer-selection", primer_selection_method_partial, name="primer_selection_method"),
    path("investigations/<int:investigation_id>/isolate/<int:isolate_id>", investigations_isolate_partial, name="isolate"),

    # LocusSets
    path("locus-sets", locus_sets, name="locus_sets"),
    path("locus-sets/<int:locus_set_id>", locus_set_detail, name="locus_set_detail"),
    path("locus-sets/create/<int:step>", create_locus_set, name="create_locus_set"),

    # MLVAConfigs
    path("investigations/<int:investigation_id>/mlva/<int:mlva_config_id>", mlva_detail, name="mlva_detail"),
    path("investigations/<int:investigation_id>/mlva/<int:mlva_config_id>/start", start_mlva_analysis, name="start_mlva_analysis"),
    path("investigations/<int:investigation_id>/mlva/<int:mlva_config_id>/progress/", get_mlva_progress, name="mlva_progress"),
    path("investigations/<int:investigation_id>/mlva/<int:mlva_config_id>/tree-data/", get_mlva_tree_data, name="mlva_tree_data"),
    
    # API endpoints
    path("api/isolates/<int:isolate_id>/toggle-active/", toggle_isolate_active, name="toggle_isolate_active"),
    path("api/mlva/<int:mlva_config_id>/toggle-all-active/", toggle_all_isolates_active, name="toggle_all_isolates_active"),
]
