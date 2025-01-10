"""Utilities for writing API handlers

See other files in this directory for implementation examples.
"""

from functools import wraps
import Ui.UiWebsocket as UiWebsocket

def requires_permission(permission):
    """Decorator for adding required permissions to API handlers"""
    def wrapper(f):
        if not hasattr(f, 'required_permissions'):
            f.required_permissions = set()
        f.required_permissions.add(permission)
        return f
    return wrapper

def ws_api_call(f):
    """Decorator for websocket API handler functions

    Usage:
    @ws_api_call
    def myCoolApi(args):
        pass

    NOTE that the function name is used as API call name
    NOTE: with current implementation ws_api_call decorator
    should be the last to apply, i.e. be the first decorator
    line.
    """
    UiWebsocket.registerApiCall(f.__name__, f)
    return f

def wrap_api_ok(f):
    """Decorator for API calls that only return ok/error response"""
    @wraps(f)
    def inner(ws, to, *args, **kwargs):
        try:
            f(ws, to, *args, **kwargs)
        except Exception as err:
            import traceback
            traceback.print_exc()
            res = {
                'error': f"Error on API call `{f.__name__}`: {err}",
            }
        else:
            res = {
                'ok': True,
            }
        ws.response(to, res)
    return inner

def wrap_api_reply(f):
    """Decorator for API calls that return single value/error to the client"""
    @wraps(f)
    def inner(ws, to, *args, **kwargs):
        try:
            res = f(ws, to, *args, **kwargs)
        # TODO: handle input errors and broken code differently
        except Exception as err:
            import traceback
            traceback.print_exc()
            res = {
                'error': f"Error on API call `{f.__name__}`: {err}",
            }
        ws.response(to, res)
    return inner
