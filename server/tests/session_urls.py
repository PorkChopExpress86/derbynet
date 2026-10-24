from django.http import HttpResponse
from django.urls import path


def write_session(request):
    request.session["probe"] = True
    return HttpResponse("ok")


urlpatterns = [path("session/", write_session)]
