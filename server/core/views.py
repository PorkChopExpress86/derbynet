from django.shortcuts import render


def health(request):
    request.session["health"] = True
    return render(request, "core/health.html")
