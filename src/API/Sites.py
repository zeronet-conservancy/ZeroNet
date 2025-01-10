"""API related to getting info/manipulation on sites
"""

from .Exceptions import BadAddress
from .Util import ws_api_call, requires_permission, wrap_api_reply
from Content import ContentDb

@ws_api_call
@requires_permission('ADMIN')
@wrap_api_reply
def siteDetails(ws, to, address):
    """Details on specified site"""
    site = ws.server.sites.get(address)
    if site is None:
        raise BadAddress(address)
    cdb = ContentDb.getContentDb()
    total_size, optional_size = cdb.getTotalSize(site)
    owned_size = cdb.getTotalSignedSize(site.address)
    return {
        'total_size': total_size,
        'optional_size': optional_size,
        'owned_size': owned_size,
    }
