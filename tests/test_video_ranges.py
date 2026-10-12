import http.client
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
import server

class VideoRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();root=Path(cls.temp.name);(root/'media').mkdir()
        cls.body=b'0123456789abcdefghijklmnopqrstuvwxyz'
        (root/'media/logireflow-walkthrough.mp4').write_bytes(cls.body)
        class TestHandler(server.Handler):
            def __init__(self,*args,**kwargs):
                server.SimpleHTTPRequestHandler.__init__(self,*args,directory=str(root),**kwargs)
            def log_message(self,*args):pass
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
        cls.thread=Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,range_value=None,method='GET'):
        connection=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=5)
        try:
            connection.request(method,'/media/logireflow-walkthrough.mp4',headers={'Range':range_value} if range_value else {})
            response=connection.getresponse();return response.status,dict(response.getheaders()),response.read()
        finally:connection.close()
    def test_complete_and_head(self):
        code,headers,body=self.request();self.assertEqual((code,body),(200,self.body))
        self.assertEqual(headers['Accept-Ranges'],'bytes')
        code,headers,body=self.request(method='HEAD');self.assertEqual(code,200)
        self.assertEqual(body,b'');self.assertEqual(int(headers['Content-Length']),len(self.body))
    def test_bounded_partial_and_head(self):
        code,headers,body=self.request('bytes=10-15');self.assertEqual((code,body),(206,self.body[10:16]))
        self.assertEqual(headers['Content-Range'],'bytes 10-15/36')
        code,headers,body=self.request('bytes=10-15',method='HEAD')
        self.assertEqual((code,body),(206,b''));self.assertEqual(headers['Content-Length'],'6')
    def test_suffix_and_open_ended(self):
        self.assertEqual(self.request('bytes=-4')[::2],(206,self.body[-4:]))
        self.assertEqual(self.request('bytes=30-')[::2],(206,self.body[30:]))
        self.assertEqual(self.request('bytes=30-999')[::2],(206,self.body[30:]))
    def test_invalid_or_outside_range(self):
        for value in ('bytes=99-','bytes=15-10','bytes=-0','bytes=','bytes=0-1,4-5'):
            with self.subTest(value=value):
                code,headers,body=self.request(value);self.assertEqual((code,body),(416,b''))
                self.assertEqual(headers['Content-Range'],'bytes */36')
