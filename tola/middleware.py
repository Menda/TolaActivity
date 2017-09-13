from django.template import Template, Context
from django.http import HttpResponse
from threading import current_thread
from rest_framework.exceptions import PermissionDenied


_current_users = {}

# TODO add test for thread safety
def get_user():
    """
    Request independent method to retrieve current user 
    from objects with no access to the current request
    :return: The current user of the requesting thread 
    """
    thread = current_thread()
    if thread not in _current_users:
        return None
    return _current_users[thread]


def remove_user():
    thread = current_thread()
    if thread in _current_users:
        del _current_users[thread]


class TolaSecurityMiddleware(object):

    def __init__(self, get_response):
        self.get_response = get_response
        # One-time configuration and initialization.
        # print "middleware init"   # debug

    def __call__(self, request):
        print "middleware called" # debug

        # Add user object to thread-dependent storage
        _current_users[current_thread()] = request.user

        response = self.get_response(request)
        return response


    def process_exception(self, request, exception):
        """
        Processes PermissionDenied Exceptions for model-level access control
        :param request: 
        :param exception: 
        :return: 
        """
        # TODO change print to log
        print("Middleware has caught an exception. exception={}".format(exception.message), type(exception))

        if type(exception) == PermissionDenied:
            t = Template("{'error':'Permission Denied'}")
            response_html = t.render(Context({}))

            response = HttpResponse(response_html)
            response.status_code = 403
            return response
