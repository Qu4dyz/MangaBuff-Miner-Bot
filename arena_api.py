"""Single-attempt game mutations: challenges/timeouts are reconciled by callers."""
from http_session import is_anti_bot_page

def get_html(session,url,**kwargs):
    """Page routes can return HTML fragments as JSON when XHR is inherited."""
    headers=getattr(session,'headers',None)
    if headers is None:return session.get(url,**kwargs)
    xhr=headers.pop('X-Requested-With',None)
    accept=headers.pop('Accept',None)
    headers['Accept']='text/html'
    try:return session.get(url,**kwargs)
    finally:
        headers.pop('Accept',None)
        if accept is not None:headers['Accept']=accept
        if xhr is not None:headers['X-Requested-With']=xhr

def post_once(session,url,**kwargs):
    if hasattr(session,'_raw_request') and hasattr(session,'_throttle'):
        if session.should_abort and session.should_abort():raise RuntimeError('module aborted')
        session._throttle()
        response=session._raw_request('POST',url,**kwargs)
        reason='http_429' if response.status_code==429 else is_anti_bot_page(response)
        if reason:
            session.last_block_reason=reason
            session._emit_block(reason,url)
        else:
            session._mark_ok('POST',url,response)
        return response
    # Test adapters and regular requests sessions perform one transport attempt.
    return session.post(url,**kwargs)
