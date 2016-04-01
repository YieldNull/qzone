#!/usr/bin/env python
# coding:utf-8

"""
Backup qzone shuoshuo using user given local Cookie

Created on 16/10/2015
"""
import urllib
import urllib2
import gzip
import cStringIO
import math
import json
import codecs
import re
import time
import sys
import sqlite3
import os
import datetime

__author__ = 'hejunjie.net'

dbconn = None  # databse connection
HOST_USER = None
HOST_USER_NAME = None

# common HTTP headers
BASE_HEADERS = [
    ('Accept', '*/*'),
    ('Accept-Encoding', 'gzip, deflate, sdch'),
    ('Accept-Language', 'en-US,en;q=0.8,zh-CN;q=0.6,zh;q=0.4'),
    ('Connection', 'keep-alive'),
    ('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)'
        'Ubuntu Chromium/45.0.2454.101 Chrome/45.0.2454.101 Safari/537.36'),
]

FRIENDS_URL = 'http://user.qzone.qq.com/p/r/cgi-bin/tfriend/friend_hat_get.cgi?' \
    'hat_seed=1&uin=865013616&fupdate=1&g_tk=22222222'

FRIENDS_HEADERS = BASE_HEADERS + [
    ('Host', 'user.qzone.qq.com'),
    # ('Referer', 'http://user.qzone.qq.com/865013616')
]

# URL of shuoshuo.  hostUin:HOST_USER uin:HOST_USER
SHUOSHUO_URL = 'http://taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6?' \
    'uin=865013616&hostUin=865013616&inCharset=utf-8&outCharset=utf-8' \
    '&notice=0&sort=0&pos=0&num=20' \
    '&cgi_host=http%3A%2F%2Ftaotao.qq.com%2Fcgi-bin%2Femotion_cgi_msglist_v6' \
    '&code_version=1&format=jsonp&need_private_comment=1&g_tk=222222'

# HTTP headers of shuoshuo
SHUOSHUO_HEADERS = BASE_HEADERS + [
    ('Host', 'taotao.qq.com'),
    ('Referer', 'http://ctc.qzs.qq.com/qzone/app/mood_v6/html/index.html'),
    #('Cookie', '')
]


def error(msg):
    print msg
    sys.exit(0)


def set_config(conf):
    global FRIENDS_URL, FRIENDS_HEADERS
    global SHUOSHUO_URL, SHUOSHUO_HEADERS
    global HOST_USER

    # cookie config
    user = conf['user']  # QQ of the user who is using this program
    HOST_USER = conf['host_user']  # QQ of the owner of the web pages

    FRIENDS_HEADERS.append(('Cookie', conf['friends']))
    SHUOSHUO_HEADERS.append(('Cookie', conf['shuoshuo']))
    FRIENDS_HEADERS.append(('Referer', 'http://user.qzone.qq.com/' + 'user'))

    # g_tk config
    shuo_skey = re.search(r'\bskey=(.*?)(;|$)', conf['shuoshuo'])
    friends_skey = re.search(r'\bp_skey=(.*?);', conf['friends'])
    if not shuo_skey:
        error('Cookie str invalid. Please login in QZone and get valid Cookie')
    shuo_gtk = calcu_gtk(shuo_skey.group(1))
    friends_gtk = calcu_gtk(friends_skey.group(1))
    FRIENDS_URL = re.sub(r'g_tk=\d+', 'g_tk=%d' % friends_gtk, FRIENDS_URL)
    SHUOSHUO_URL = re.sub(r'g_tk=\d+', 'g_tk=%d' % shuo_gtk, SHUOSHUO_URL)

    # user and host user config
    SHUOSHUO_URL = re.sub(r'uin=\d+&hostUin=\d+',
                          'uin=%s&hostUin=%s' % (HOST_USER, HOST_USER),
                          SHUOSHUO_URL)
    FRIENDS_URL = re.sub(r'uin=\d+', 'uin=%s' % user, FRIENDS_URL)


def create_db():
    sql = """
        CREATE TABLE friends(
            qqnum       INTEGER PRIMARY KEY,
            name        TEXT
        );

        CREATE TABLE shuoshuo(
            timestamp       INTEGER,
            content         TEXT,
            rt_qqnum        INTEGER,
            rt_timestamp    INTEGER,
            rt_content      TEXT,
            FOREIGN KEY(rt_qqnum) REFERENCES friends(qqnum)
        );

        CREATE TABLE comments(
            timestamp       INTEGER,
            content         TEXT,
            qqnum           TEXT NOT NULL,
            to_shuoshuo     INTEGER,
            to_user         INTEGER,
            to_comment      INTEGER,
            FOREIGN KEY(to_shuoshuo) REFERENCES shuoshuo(timestamp),
            FOREIGN KEY(to_comment) REFERENCES comments(timestamp),
            FOREIGN KEY(to_user) REFERENCES friends(qqnum)
        );

        CREATE TABLE pictures(
            id				INTEGER PRIMARY KEY AUTOINCREMENT,
            url				TEXT NOT NULL,
            to_shuoshuo		INTEGER,
            to_comment    	INTEGER,
            FOREIGN KEY(to_shuoshuo) REFERENCES shuoshuo(timestamp),
            FOREIGN KEY(to_comment) REFERENCES comment(timestamp)
        );
    """
    cursor = dbconn.cursor()
    cursor.executescript(sql)
    dbconn.commit()


def calcu_gtk(skey):
    """
    calculate 'g_tk' from 'skey' in cookie
    """
    b, c = 5381, 0
    while c < len(skey):
        b += (b << 5) + ord(skey[c])
        c += 1
    return b & 2147483647


def do_http(url, headers, encoding='utf-8'):
    """
    send http request to server using GET
    :param headers: http headers
    :param encoding: decode data from server using that encoding
    :return: response from server as unicode
    """
    opener = urllib2.build_opener()
    opener.addheaders = headers
    res = opener.open(url)

    # un-gzip
    unziped = gzip.GzipFile(fileobj=cStringIO.StringIO(res.read()))
    content = unziped.read().decode(encoding)
    opener.close()
    return content


def decode_jsonp(jsonp):
    """
    transform jsonp data ( as the form of "_Callback(.*);" ) to json
    :return: transformed data
    """
    jsonp = jsonp.strip()
    return jsonp[10:len(jsonp) - 2]


def get_friends():
    """
    get firiends and store in db
    """
    print 'Getting friends list......'
    data = do_http(FRIENDS_URL, FRIENDS_HEADERS)
    data = json.loads(decode_jsonp(data))
    cursor = dbconn.cursor()

    print 'Storing firiends info......'
    for key, value in data['data'].items():
        try:
            qq = int(key)
        except ValueError:
            continue
        cursor.execute('INSERT INTO friends(qqnum,name) VALUES(?,?)',
                       (qq, value['realname']))
    dbconn.commit()


def store_shuoshuo(data):
    """
    store shuoshuo data to db
    :param data: json data
    """
    cursor = dbconn.cursor()
    for root in data['msglist']:
        # base
        content = root['content']
        timestamp = root['created_time']

        # share
        rt_qqnum = root.get('rt_uin')
        rt_timestamp = root.get('rt_createTime')
        rt_content = root.get('rt_con')
        if rt_content:
            rt_content = rt_content['content']
            rt_timestamp = int(time.mktime(datetime.datetime.strptime(
                rt_timestamp.encode('utf-8'),
                '%Y年%m月%d日').timetuple()))

        # I once got an IntegrityError which says that timestamp is duplicated
        try:
            cursor.execute('INSERT INTO shuoshuo('
                           'content,timestamp,rt_qqnum,rt_content,rt_timestamp) VALUES(?,?,?,?,?)',
                           (content, timestamp, rt_qqnum, rt_content, rt_timestamp))
        except IntegrityError:
            # error('Unkonwn error encountered. Pelase retry')
            print '%d duplicated' % timestamp
            continue

        # pictures
        pic = root.get('pic')
        if pic:
            for p in pic:
                cursor.execute('INSERT INTO pictures('
                               'url,to_shuoshuo) VALUES(?,?)',
                               (p['url2'], timestamp))
        # comments
        comlist = root.get('commentlist')
        if comlist:
            for comment in comlist:
                # comment content
                c_content = comment['content']
                c_timestamp = comment['create_time']
                c_qqnum = comment['uin']
                cursor.execute('INSERT INTO comments('
                               'timestamp,content,qqnum,to_shuoshuo) VALUES(?,?,?,?)',
                               (c_timestamp, c_content, c_qqnum, timestamp))
                # comment pictures
                c_pic = comment.get('pic')
                if c_pic:
                    for p in c_pic:
                        cursor.execute('INSERT INTO pictures('
                                       'url,to_comment) VALUES(?,?)',
                                       (p['hd_url'], c_timestamp))
                # comment to comment
                reply = comment.get('list_3')
                if reply:
                    for rep in reply:
                        r_content = rep['content']
                        r_timestamp = rep['create_time']
                        r_qqnum = rep['uin']
                        try:
                            r_user = re.search(
                                r'^@{uin:(\d+),.*?}', r_content).group(1)
                            r_content = re.sub(r'^@{.*?}', '', r_content)
                        except AttributeError:
                            r_user = HOST_USER
                        cursor.execute('INSERT INTO comments('
                                       'timestamp,content,qqnum,to_comment,to_user)'
                                       'VALUES(?,?,?,?,?)',
                                       (r_timestamp, r_content,
                                        r_qqnum, c_timestamp, r_user))
    dbconn.commit()


def get_shuoshuo():
    print 'Dowloading shuoshuo......'

    i, per = 0, 40.0
    msgsum = math.pow(2, 32)
    while True:  # `per` entries per query
        print 'Sending HTTP request......'
        url = re.sub(r'pos=\d+&num=\d+',
                     'pos=%d&num=%d' % (i * per, per), SHUOSHUO_URL)
        res = do_http(url, SHUOSHUO_HEADERS)
        res = decode_jsonp(res)
        res = json.loads(res)

        if i == 0:  # get the amount at the first query
            global HOST_USER_NAME
            msgsum = res['usrinfo']['msgnum']
            HOST_USER_NAME = res['usrinfo']['name']
        i += 1

        store_shuoshuo(res)
        print 'Got %d/%d. Storing to database......' % (
            i * per if i * per < msgsum else msgsum, msgsum)

        if i * per >= msgsum:
            break


def download_shuoshuo():
    global dbconn
    db = '%s.db' % HOST_USER
    if os.path.exists(db):
        os.remove(db)
    dbconn = sqlite3.connect(db)
    create_db()

    get_friends()
    get_shuoshuo()

    re_db = '%s_%s.db' % (HOST_USER, HOST_USER_NAME)
    os.rename(db, re_db)
    print u'Work done! Data was stored in "%s"' % re_db

if __name__ == '__main__':
    if len(sys.argv) != 2:
        error('Usage: shuoshuo.py conf.json')

    # load config
    with open(sys.argv[1], 'r') as f:
        conf = json.load(f)
    set_config(conf)

    download_shuoshuo()
