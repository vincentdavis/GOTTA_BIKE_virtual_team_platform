"""Project middleware.

Currently one class, which exists to let Railway's private network work without weakening
the public site.
"""

from typing import TYPE_CHECKING

from django.conf import settings
from django.http.request import split_domain_port
from django.middleware.security import SecurityMiddleware

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


class PrivateNetworkAwareSecurityMiddleware(SecurityMiddleware):
    """Django's SecurityMiddleware, minus the HTTPS redirect for private-network traffic.

    ``SECURE_SSL_REDIRECT`` bounces any request that did not arrive over HTTPS. That is right
    for the public site, but it makes Railway's private network unusable: TLS terminates at
    Railway's edge, and internal traffic bypasses the edge entirely, so a call from the
    Discord bot to ``coalitionapp.railway.internal`` arrives as plain HTTP and is answered
    with a 301 to a URL the bot cannot usefully follow.

    The obvious alternative is worse. ``SECURE_REDIRECT_EXEMPT`` matches on path, so exempting
    ``^api/dbot/`` would also stop redirecting *public* HTTP requests to those routes -- and
    those requests carry the bot API key, which would then travel in clear text. Matching on
    the host keeps the exemption to traffic that never touched the internet.

    Only the redirect is skipped. Everything else SecurityMiddleware does, including the
    response headers, is untouched, because those are applied on the way out.
    """

    def process_request(self, request: HttpRequest) -> HttpResponse | None:
        """Skip the HTTPS redirect when the request came in over the private network.

        Args:
            request: The incoming request.

        Returns:
            None to continue processing, or SecurityMiddleware's redirect response.

        """
        private_domain = getattr(settings, "RAILWAY_PRIVATE_DOMAIN", "")
        if private_domain:
            host, _port = split_domain_port(request.get_host())
            if host == private_domain:
                return None
        return super().process_request(request)
