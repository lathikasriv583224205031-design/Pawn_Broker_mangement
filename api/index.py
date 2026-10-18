import os
import sys

# Add root directory to sys.path so app can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

class VercelPathMiddleware:
    """
    Fixes Vercel routing where the rewritten function path (/api/index or /api/index.py)
    is passed to Flask's WSGI environment instead of the user's requested path,
    which was causing Flask to return 404 Not Found.
    """
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        # 1. Check if Vercel set X-Forwarded-Uri or X-Matched-Path
        forwarded_uri = environ.get('HTTP_X_FORWARDED_URI') or environ.get('HTTP_X_MATCHED_PATH')
        if forwarded_uri:
            environ['PATH_INFO'] = forwarded_uri.split('?')[0]
        else:
            path_info = environ.get('PATH_INFO', '')
            for prefix in ['/api/index.py', '/api/index']:
                if path_info.startswith(prefix):
                    path_info = path_info[len(prefix):]
                    if not path_info or not path_info.startswith('/'):
                        path_info = '/' + path_info
                    environ['PATH_INFO'] = path_info
                    break

        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelPathMiddleware(app.wsgi_app)
