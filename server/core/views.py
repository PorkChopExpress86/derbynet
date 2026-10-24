from django.shortcuts import render


def health(request):
    return render(request, "core/health.html")
