"""
URL configuration for stats app.
"""

from django.urls import path

from . import views

app_name = "stats"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("matches/", views.matches_list, name="matches"),
    path("match/<int:match_id>/", views.match_detail, name="match_detail"),
    path("match/<int:match_id>/replay/", views.match_replay, name="match_replay"),
    path("decks/", views.decks_list, name="decks"),
    path("deck/<int:deck_id>/", views.deck_detail, name="deck_detail"),
    path("deck/<int:deck_id>/gallery/", views.deck_gallery, name="deck_gallery"),
    path("import/", views.import_log, name="import_log"),
    path("imports/", views.import_sessions, name="import_sessions"),
    path("card-data/", views.card_data, name="card_data"),
    path("api/stats/", views.api_stats, name="api_stats"),
]
