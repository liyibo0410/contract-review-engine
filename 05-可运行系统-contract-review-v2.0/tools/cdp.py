#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""极简 CDP（Chrome DevTools Protocol）客户端 —— 仅用标准库。

为什么需要它：`--dump-dom` 只能拿到 HTML 结构，量不出「布局塌陷、元素重叠、
横向溢出、脚本报错」这类**只有真正跑起来才知道**的问题。而「前端还是有问题」
恰恰绝大多数属于这一类。

CDP 的原生接口是 WebSocket，Python 标准库没有 WS 客户端，所以这里手写一个
最小实现：握手 + 客户端掩码文本帧 + 分片长度解析。只支持文本帧、不做压缩，
对调试协议足够（CDP 消息是 JSON 文本）。

对外接口：
    with Browser(chrome_path, user_data_dir) as br:
        page = br.new_page(cookie=("cr_session", token), url=...)
        page.wait_load()
        page.errors          # 脚本异常 / console.error 清单
        page.eval(js)        # 求值并取回 JSON
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import subprocess
import time
import urllib.request

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ------------------------------------------------------------------ WebSocket
class WS:
    """最小 WebSocket 文本帧客户端（客户端 → 服务端需掩码）。"""

    def __init__(self, url, timeout=30):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)$", url)
        if not m:
            raise ValueError("不支持的 ws 地址：%s" % url)
        self.host, self.port, self.path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        self._handshake()

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (self.path, self.host, self.port, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError("WebSocket 握手被对端关闭")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise IOError("WebSocket 握手失败：%s" % head[:120])
        self._buf = rest

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise IOError("连接已关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text):
        payload = text.encode("utf-8")
        n = len(payload)
        header = bytearray([0x81])                     # FIN + text
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            data = self._recv_exact(length) if length else b""
            if opcode == 0x8:                          # close
                raise IOError("对端关闭")
            if opcode == 0x9:                          # ping → pong
                continue
            if opcode in (0x1, 0x2):
                return data.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ 浏览器
DEFAULT_CHROME = next((p for p in [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
] if os.path.exists(p)), "")


class Page:
    def __init__(self, br, session_id):
        self.br = br
        self.sid = session_id
        self.errors = []
        self.console = []

    def _send(self, method, **params):
        return self.br.send(method, sessionId=self.sid, **params)

    def eval(self, expr, timeout=30):
        r = self._send("Runtime.evaluate",
                       expression=expr, returnByValue=True, awaitPromise=True)
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return {"_exception": str(res["exceptionDetails"])[:400]}
        return (res.get("result") or {}).get("value")

    def wait_load(self, timeout=25.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.br.loaded.get(self.sid):
                break
            self.br.pump(0.25)
        # 再多跑一会儿，等异步数据填进来（页面里的 fetch + 渲染）
        t1 = time.time()
        while time.time() - t1 < 2.6:
            self.br.pump(0.3)
        return True

    def navigate(self, url, cookie=None):
        if cookie:
            self._send("Network.setCookie", name=cookie[0], value=cookie[1],
                       url=url.split("/", 3)[0] + "//" + url.split("/")[2] + "/",
                       path="/")
        self.br.loaded[self.sid] = False
        self._send("Page.navigate", url=url)

    def screenshot(self, path, width=1600, height=1100):
        self._send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                   deviceScaleFactor=1, mobile=False)
        r = self._send("Page.captureScreenshot", format="png")
        data = (r.get("result") or {}).get("data") or r.get("data")
        if not data:
            return False
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return True


class Browser:
    def __init__(self, chrome=DEFAULT_CHROME, ud=None, headless=True):
        self.chrome = chrome
        self.ud = ud
        self.proc = None
        self.ws = None
        self._id = 0
        self._pending = {}
        self.loaded = {}
        self.events = []

    # ---------------- 连接
    def start(self, timeout=25):
        args = [self.chrome, "--headless=new" if True else "--headless",
                "--disable-gpu", "--no-sandbox", "--no-first-run",
                "--hide-scrollbars", "--disable-dev-shm-usage",
                "--remote-debugging-port=0", "--user-data-dir=" + self.ud,
                "about:blank"]
        env = {k: v for k, v in os.environ.items()
               if k.lower() not in ("http_proxy", "https_proxy", "no_proxy")}
        self.proc = subprocess.Popen(args, env=env,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        port_file = os.path.join(self.ud, "DevToolsActivePort")
        t0 = time.time()
        port = None
        while time.time() - t0 < timeout:
            if os.path.exists(port_file):
                try:
                    with open(port_file) as f:
                        port = int(f.readline().strip())
                    if port:
                        break
                except (OSError, ValueError):
                    pass
            time.sleep(0.2)
        if not port:
            raise IOError("拿不到 DevTools 端口（Chrome 未正常启动）")
        info = json.loads(_NO_PROXY.open(
            "http://127.0.0.1:%d/json/version" % port, timeout=15).read().decode())
        self.ws = WS(info["webSocketDebuggerUrl"])
        return self

    def send(self, method, sessionId=None, **params):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params}
        if sessionId:
            msg["sessionId"] = sessionId
        self.ws.send(json.dumps(msg))
        t0 = time.time()
        while time.time() - t0 < 40:
            if mid in self._pending:
                r = self._pending.pop(mid)
                if "error" in r:
                    raise IOError("%s 失败：%s" % (method, r["error"]))
                return r
            self.pump(0.05)
        raise IOError("%s 超时" % method)

    def pump(self, timeout=0.2):
        """读取并分发事件，返回是否有消息。"""
        try:
            self.ws.sock.settimeout(timeout)
            raw = self.ws.recv()
        except (socket.timeout, TimeoutError):
            return False
        except OSError:
            return False
        try:
            msg = json.loads(raw)
        except ValueError:
            return False
        if "id" in msg:
            self._pending[msg["id"]] = msg
            return True
        m = msg.get("method")
        sid = msg.get("sessionId")
        if m == "Page.loadEventFired":
            self.loaded[sid] = True
        elif m == "Runtime.exceptionThrown":
            d = (msg.get("params") or {}).get("exceptionDetails") or {}
            text = (d.get("exception") or {}).get("description") or d.get("text") or ""
            self.events.append(("error", sid, text.split("\n")[0][:220]))
        elif m == "Runtime.consoleAPICalled":
            p = msg.get("params") or {}
            if p.get("type") in ("error", "warning"):
                parts = [str((a or {}).get("value") or (a or {}).get("description") or "")
                         for a in (p.get("args") or [])]
                self.events.append((p.get("type"), sid,
                                    " ".join(parts)[:220]))
        elif m == "Log.entryAdded":
            e = (msg.get("params") or {}).get("entry") or {}
            if e.get("level") in ("error", "warning"):
                self.events.append((e.get("level"), sid,
                                    ("%s %s" % (e.get("text") or "", e.get("url") or ""))[:220]))
        return True

    # ---------------- 页面
    def new_page(self, url="about:blank"):
        r = self.send("Target.createTarget", url="about:blank")
        tid = r["result"]["targetId"]
        r2 = self.send("Target.attachToTarget", targetId=tid, flatten=True)
        sid = r2["result"]["sessionId"]
        pg = Page(self, sid)
        pg._send("Page.enable")
        pg._send("Runtime.enable")
        pg._send("Network.enable")
        pg._send("Log.enable")
        return pg

    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
        except OSError:
            pass
        try:
            if self.ws:
                self.ws.close()
        except OSError:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.close()
