#! /usr/bin/env python3

"""
备份QQ空间：说说，日志，相册。

仅支持MacOS Chrome and Linux Chromium
先从浏览器手动登陆，然后从本地Cookie文件中提取所需数据，模拟登陆。


提取Cookie参见  https://github.com/n8henrie/pycookiecheat

Created By YieldNull at 4/1/16
"""

import json
import sqlite3
import os.path
import keyring
import sys
import requests
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from pymongo import MongoClient

client = MongoClient()
db = client.qzone

base_headers = {
    'Accept': '*/*',
    'Accept-Encoding': 'gzip, deflate, sdch',
    'Accept-Language': 'en-US,en;q=0.8,zh-CN;q=0.6,zh;q=0.4',
    'Connection': 'keep-alive',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)'
                  'Ubuntu Chromium/45.0.2454.101 Chrome/45.0.2454.101 Safari/537.36',
}


class LoginException(Exception):
    """
    登陆失败
    """

    def __init__(self, msg=None):
        if msg:
            self.msg = msg
        else:
            self.msg = "未知错误。请重新登陆QQ空间并进入说说页面"


class FetchException(Exception):
    def __init__(self, msg):
        self.msg = msg


def chrome_cookies(host_key):
    """
    从Chrome中获取Cookie

    Source from https://github.com/n8henrie/pycookiecheat. Modified.

    :param host_key: host_key in cookie, like ".qq.com"
    :return: cookies. 可直接传给requests
    """
    salt = b'saltysalt'
    iv = b' ' * 16
    length = 16

    def chrome_decrypt(encrypted_value, key=None):

        # Encrypted cookies should be prefixed with 'v10' according to the
        # Chromium code. Strip it off.
        encrypted_value = encrypted_value[3:]

        # Strip padding by taking off number indicated by padding
        # eg if last is '\x0e' then ord('\x0e') == 14, so take off 14.
        # You'll need to change this function to use ord() for python2.
        def clean(x):
            return x[:-x[-1]].decode('utf8')

        cipher = AES.new(key, AES.MODE_CBC, IV=iv)
        decrypted = cipher.decrypt(encrypted_value)

        return clean(decrypted)

    # If running Chrome on OSX
    if sys.platform == 'darwin':
        my_pass = keyring.get_password('Chrome Safe Storage', 'Chrome')
        my_pass = my_pass.encode('utf8')
        iterations = 1003
        cookie_file = os.path.expanduser(
            '~/Library/Application Support/Google/Chrome/Default/Cookies'
        )

    # If running Chromium on Linux
    elif sys.platform == 'linux':
        my_pass = 'peanuts'.encode('utf8')
        iterations = 1
        cookie_file = os.path.expanduser(
            '~/.config/chromium/Default/Cookies'
        )
    else:
        raise Exception("This script only works on OSX or Linux.")

    # Generate key from values above
    key = PBKDF2(my_pass, salt, length, iterations)

    conn = sqlite3.connect(cookie_file)

    sql = 'SELECT name, value, encrypted_value FROM cookies WHERE host_key = "{:s}"'.format(host_key)

    cookies = {}
    cookies_list = []

    with conn:
        for k, v, ev in conn.execute(sql):

            # if there is a not encrypted value or if the encrypted value
            # doesn't start with the 'v10' prefix, return v
            if v or (ev[:3] != b'v10'):
                cookies_list.append((k, v))
            else:
                decrypted_tuple = (k, chrome_decrypt(ev, key=key))
                cookies_list.append(decrypted_tuple)
        cookies.update(cookies_list)

    return cookies


def gen_gtk(skey):
    """
    从cookie中的‘skey’计算出‘g_tk’

    See http://ctc.qzonestyle.gtimg.cn/ac/qzone/qzfl/qzfl_v8_2.1.45.js

    :param skey: skey
    :return: g_tk : str
    """
    b, c = 5381, 0
    while c < len(skey):
        b += (b << 5) + ord(skey[c])
        c += 1
    return str(b & 2147483647)


class API:
    def __init__(self, qq, gtk):
        self.qq = qq
        self.gtk = gtk

    def url_journal(self, pos, num):
        url = 'https://h5.qzone.qq.com/proxy/domain/b1.qzone.qq.com/cgi-bin/blognew/get_abs?' \
              'hostUin={qq}&uin={qq}&blogType=0&cateName=&cateHex=' \
              '&statYear=2016&reqInfo=7&pos={pos}&num={num}&sortType=0' \
              '&source=0&ref=qzone' \
              '&g_tk={gtk}&verbose=1'

        return url.format(qq=self.qq, gtk=self.gtk, pos=pos, num=num)

    def url_mood(self, pos, num):
        """
        生成获取说说API 的URL

        :param pos: 偏移量，最新的一条说说记为0
        :param num: 从pos开始，获取多少条纪录
        :return: url
        """
        url = 'http://taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6?uin={qq}' \
              '&inCharset=utf-8&outCharset=utf-8&hostUin={qq}' \
              '&notice=0&sort=0&pos={pos}&num={num}' \
              '&cgi_host=http%3A%2F%2Ftaotao.qq.com%2Fcgi-bin%2Femotion_cgi_msglist_v6' \
              '&code_version=1&format=jsonp&need_private_comment=1&g_tk={gtk}'

        return url.format(qq=self.qq, gtk=self.gtk, pos=pos, num=num)


class Fetcher:
    def __init__(self, qq):
        self.qq = qq
        self.cookies = chrome_cookies('.qq.com')

        skey = self.cookies.get('skey')
        if skey is None:
            raise LoginException('请先登陆空间，并进入说说页面')

        gtk = gen_gtk(skey)

        self.api = API(qq, gtk)

    def fetch_mood(self, handler):
        """
        获取所有可见说说，存入数据库或交由handler处理

        :param handler: 处理每次获取到的数据 handler(data : dict)
        """

        headers = base_headers.copy()
        headers.update({
            'Referer': 'http://ctc.qzs.qq.com/qzone/app/mood_v6/html/index.html',
            'Host': 'taotao.qq.com'
        })

        name = self.qq
        amount = 10000000
        got = 0

        pos = 0
        num = 40
        while pos < amount:
            try:
                data = self._http_request(pos, num, headers)
            except FetchException as e:
                print(e.msg, 'Retrying...')
                continue

            if data.get('msglist') is None:  # 被存档，不可见
                return 0

            msg_list = data['msglist']
            _len = len(msg_list)

            got += _len
            if _len == 0:  # 结束，已获取全部
                break

            user_info = data['usrinfo']
            name = user_info['name']
            amount = user_info['msgnum']

            handler(msg_list)  # 存储得到的数据
            print('[MOOD] Amount:{:d}, Got: {:d}'.format(amount, got))

            pos += num

        return name, amount, got

    @staticmethod
    def _decode_jsonp(jsonp):
        """
        将服务器返回的jsonp数据处理成json数据。

        :param jsonp: jsonp格式的字符串：“_Callback(.*);”
        :return: json格式的字符串
        """
        jsonp = jsonp.strip()
        return jsonp[10:len(jsonp) - 2]

    def _http_request(self, pos, num, headers=base_headers):
        """
        发送HTTP请求
        :param pos: pos in api
        :param num: num in api
        :param headers: http header
        :return: 得到的JSON数据
        """
        url = self.api.url_mood(pos, num)
        res = requests.get(url, headers=headers, cookies=self.cookies)

        code = res.status_code
        source = self._decode_jsonp(res.text)

        print('[HTTP {:d}] Position:{:d} '.format(code, pos))

        # 无法正常获取或解析数据
        if code != 200:
            raise FetchException('HTTP Request Failed')
        try:
            data = json.loads(source)
        except ValueError:
            raise FetchException('Not A JSON FILE')

        # 正常解析数据，但返回 error message
        message = data.get('message')
        if message is None:
            raise LoginException()
        elif len(message) > 0:
            raise LoginException(message)

        return data


def backup_mood(fetcher):
    """
    备份说说
    """

    def mood_handler(data):
        db.mood.insert_many(data)
        # print('[Mongodb] Store {:d} documents'.format(len(data)))

    try:
        name, amount, got = fetcher.fetch_mood(mood_handler)
        print('\nQQ:{:s} Nickname:{:s} Amount:{:d} Got:{:d}\n'.format(fetcher.qq, name, amount, got))

        if got != amount:
            print('The user archived some moods which are invisible.')

    except LoginException as e:
        print(e.msg)


def backup_journal(fetcher):
    """
    备份日志
    :param fetcher
    """
    raise Exception('Not yet implemented')


def backup_photo(fetcher):
    """
    备份相册

    :param fetcher
    """
    raise Exception('Not yet implemented')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Backup qzone. https://github.com/YieldNull')

    parser.add_argument('-m', dest='mood', action='store_true',
                        help='Backup shuoshuo')

    parser.add_argument('-j', dest='journal', action='store_true',
                        help='Backup journal')

    parser.add_argument('-p', dest='photo', action='store_true',
                        help='Backup photo')

    parser.add_argument('qq', metavar='QQ', type=int,
                        help='The QQ number of the user you want to backup')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    ft = Fetcher(str(args.qq))

    if args.mood:
        backup_mood(ft)

    if args.journal:
        backup_journal(ft)

    if args.photo:
        backup_photo(ft)
