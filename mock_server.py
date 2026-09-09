#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟机场订阅服务器，用于本地测试"""
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer

# 模拟 3 个节点
nodes = [
    "vless://a1b2c3d4-e5f6-7890-abcd-ef1234567890@hk1.example.com:443?security=tls&sni=hk1.example.com#🇭🇰 香港01",
    "vless://a1b2c3d4-e5f6-7890-abcd-ef1234567890@jp1.example.com:443?security=tls&sni=jp1.example.com#🇯🇵 日本01",
    "trojan://password123@us1.example.com:443?security=tls#🇺🇸 美国01",
    "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQxMjM@sg1.example.com:8388#🇸🇬 新加坡01",
    "vmess://eyJ2IjoiMiIsInBzIjoi8J+UhSDlj7Dpn7PvvIzmsJHmlrDjgIIiLCJhZGQiOiJ0dzEuZXhhbXBsZS5jb20ifQ==",
]
body = base64.b64encode("\n".join(nodes).encode()).decode()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 标准机场响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("subscription-userinfo", "upload=5368709120; download=21474836480; total=107374182400; expire=1893456000")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    print("Mock airport server on :8899")
    HTTPServer(("127.0.0.1", 8899), Handler).serve_forever()
