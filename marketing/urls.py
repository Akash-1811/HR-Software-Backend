from django.urls import path

from marketing import views

urlpatterns = [
    path("book-demo/", views.book_demo, name="marketing-book-demo"),
]
