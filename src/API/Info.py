from .Util import ws_api_call, requires_permission

@ws_api_call
def ping(ws, to):
    """Ping websocket connection"""
    ws.response(to, 'pong')

@ws_api_call
def serverInfo(ws, to):
    """Get server info"""
    ws.response(to, ws.formatServerInfo())
