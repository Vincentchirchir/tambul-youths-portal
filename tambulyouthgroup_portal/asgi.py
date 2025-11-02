"""
ASGI config for tambulyouthgroup_portal project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from core import routing as core_routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tambulyouthgroup_portal.settings')

application = get_asgi_application()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tambulyouthgroup_portal.settings")

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(core_routing.websocket_urlpatterns)
    ),
})