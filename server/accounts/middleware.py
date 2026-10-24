from accounts.permissions import granted, role


class RoleSessionMiddleware:
    """Sets `request.role` and `request.permissions` for views and templates.

    They are read when the request arrives. A view that logs in or out in the
    same request reads `granted()` and `role()` instead.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.role = role(request)
        request.permissions = granted(request)
        return self.get_response(request)
