from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.card_index, name="index"),
    path("upload/", views.upload_card, name="upload"),
    path("add/", views.add_paper_card, name="add_paper_card"),
    path("card/<int:pk>/", views.card_detail, name="card_detail"),
    path("card/<int:pk>/lookup/", views.name_lookup, name="name_lookup"),
    path("paper/<int:pk>/", views.paper_card_detail, name="paper_card_detail"),
    path("photography-guide/", views.card_photography_guide, name="photography_guide"),
]
