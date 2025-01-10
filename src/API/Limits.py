from .Util import ws_api_call, requires_permission, wrap_api_ok
from Content import ContentDb

@ws_api_call
@requires_permission('ADMIN')
def getSizeLimitRules(ws, to):
    """Returns all size limit rules"""
    cdb = ContentDb.getContentDb()
    res = cdb.getSizeLimitRules()
    from rich import print
    print(res)
    ws.response(to, res)

@ws_api_call
@requires_permission('ADMIN')
@wrap_api_ok
def addPrivateSizeLimitRule(ws, to, address, rule, value, priority):
    cdb = ContentDb.getContentDb()
    cdb.addPrivateSizeLimitRule(address, rule, value, priority)

@ws_api_call
@requires_permission('ADMIN')
@wrap_api_ok
def removePrivateSizeLimitRule(ws, to, rule_id):
    cdb = ContentDb.getContentDb()
    cdb.removePrivateSizeLimitRule(rule_id)
