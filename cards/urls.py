from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.card_index, name="index"),
    path("add/", views.add_paper_card, name="add_paper_card"),
    path("paper/<int:pk>/", views.paper_card_detail, name="paper_card_detail"),
]
