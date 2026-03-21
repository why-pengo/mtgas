from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.card_index, name="index"),
    path("upload/", views.upload_card, name="upload"),
    path("card/<int:pk>/", views.card_detail, name="card_detail"),
    path("photography-guide/", views.card_photography_guide, name="photography_guide"),
]
