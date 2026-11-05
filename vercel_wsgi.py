"""WSGI entrypoint for the existing HTTP handlers, preserving original request paths."""
import io
from types import SimpleNamespace

class MemoryConnection:
    def __init__(self,request):self.input=io.BytesIO(request);self.output=io.BytesIO()
    def makefile(self,mode,*args,**kwargs):return self.input if 'r' in mode else self.output
    def sendall(self,data):self.output.write(data)
    def settimeout(self,value):pass
    def shutdown(self,*args):pass

def application(environ,start_response):
    from server import BlocksHandler
    method=environ.get('REQUEST_METHOD','GET')
    path=environ.get('SCRIPT_NAME','')+environ.get('PATH_INFO','/')
    query=environ.get('QUERY_STRING','')
    if query:path+='?'+query
    size=int(environ.get('CONTENT_LENGTH') or 0)
    body=environ['wsgi.input'].read(size) if size else b''
    headers={'Host':environ.get('HTTP_HOST',environ.get('SERVER_NAME','localhost')),'Connection':'close','Content-Length':str(len(body))}
    for name,value in environ.items():
        if name.startswith('HTTP_') and name not in ('HTTP_CONNECTION','HTTP_HOST','HTTP_CONTENT_LENGTH','HTTP_TRANSFER_ENCODING'):
            headers[name[5:].replace('_','-')]=str(value)
    if environ.get('CONTENT_TYPE'):headers['Content-Type']=environ['CONTENT_TYPE']
    if environ.get('wsgi.url_scheme')=='https':headers['X-Forwarded-Proto']='https'
    request=(f'{method} {path} HTTP/1.0\r\n'+''.join(f'{name}: {value}\r\n' for name,value in headers.items())+'\r\n').encode('iso-8859-1')+body
    connection=MemoryConnection(request)
    address=(environ.get('REMOTE_ADDR','127.0.0.1'),0)
    server=SimpleNamespace(server_name=environ.get('SERVER_NAME','localhost'),server_port=int(environ.get('SERVER_PORT') or 80))
    BlocksHandler(connection,address,server)
    response=connection.output.getvalue()
    raw_headers,separator,payload=response.partition(b'\r\n\r\n')
    if not separator:
        start_response('500 Internal Server Error',[('Content-Type','text/plain; charset=utf-8')]);return [b'Invalid application response']
    lines=raw_headers.decode('iso-8859-1').split('\r\n')
    status=lines[0].split(' ',1)[1]
    response_headers=[]
    for line in lines[1:]:
        key,value=line.split(':',1)
        if key.casefold() not in ('connection','transfer-encoding','keep-alive'):response_headers.append((key,value.strip()))
    if not any(name.casefold()=='content-type' for name,value in response_headers):response_headers.append(('Content-Type','text/plain; charset=utf-8'))
    if not any(name.casefold()=='content-length' for name,value in response_headers):response_headers.append(('Content-Length',str(len(payload))))
    start_response(status,response_headers)
    return [payload[offset:offset+16384] for offset in range(0,len(payload),16384)] or [b'']
