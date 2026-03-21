from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("upload/", views.upload_card, name="upload"),
    path("card/<int:pk>/", views.card_detail, name="card_detail"),
]
