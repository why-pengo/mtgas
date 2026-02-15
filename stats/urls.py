"""
URL configuration for stats app.
"""

from django.urls import path
from . import views

app_name = 'stats'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('matches/', views.matches_list, name='matches'),
    path('match/<int:match_id>/', views.match_detail, name='match_detail'),
    path('decks/', views.decks_list, name='decks'),
    path('deck/<int:deck_id>/', views.deck_detail, name='deck_detail'),
    path('imports/', views.import_sessions, name='import_sessions'),
    path('api/stats/', views.api_stats, name='api_stats'),
]

