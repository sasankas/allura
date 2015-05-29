# -*- coding: utf-8 -*-

#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from __future__ import unicode_literals
import sys
import os
import os.path
import difflib
import urllib.request, urllib.parse, urllib.error
import urllib.request, urllib.error, urllib.parse
import re
import json
import logging
import string
import random
import pickle as pickle
from hashlib import sha1
from datetime import datetime, timedelta
from collections import defaultdict
import shlex
import socket
from functools import partial

import tg
import genshi.template
import chardet
import pkg_resources
from formencode.validators import FancyValidator
from dateutil.parser import parse
from bson import ObjectId
from paste.deploy import appconfig
from pymongo.errors import InvalidId
from contextlib import contextmanager
from pylons import tmpl_context as c, app_globals as g
from pylons import response, request
from tg.decorators import before_validate
from formencode.variabledecode import variable_decode
import formencode
from jinja2 import Markup
from jinja2.filters import contextfilter, escape
from paste.deploy.converters import asbool, aslist

from webhelpers import date, feedgenerator, html, number, misc, text
from webob.exc import HTTPUnauthorized

from allura.lib import exceptions as exc
from allura.lib import AsciiDammit
from allura.lib import utils

# import to make available to templates, don't delete:
from .security import has_access


log = logging.getLogger(__name__)

# validates project, subproject, and user names
re_project_name = re.compile(r'^[a-z][-a-z0-9]{2,14}$')

# validates tool mount point names
re_tool_mount_point = re.compile(r'^[a-z][-a-z0-9]{0,62}$')
re_tool_mount_point_fragment = re.compile(r'[a-z][-a-z0-9]*')
re_relaxed_tool_mount_point = re.compile(
    r'^[a-zA-Z0-9][-a-zA-Z0-9_\.\+]{0,62}$')
re_relaxed_tool_mount_point_fragment = re.compile(
    r'[a-zA-Z0-9][-a-zA-Z0-9_\.\+]*')

re_clean_vardec_key = re.compile(r'''\A
( # first part
\w+# name...
(-\d+)?# with optional -digits suffix
)
(\. # next part(s)
\w+# name...
(-\d+)?# with optional -digits suffix
)+
\Z''', re.VERBOSE)

# markdown escaping regexps
re_amp = re.compile(r'''
    [&]          # amp
    (?=          # look ahead for:
      ([a-zA-Z0-9]+;)  # named HTML entity
      |
      (\#[0-9]+;)      # decimal entity
      |
      (\#x[0-9A-F]+;)  # hex entity
    )
    ''', re.VERBOSE)
re_leading_spaces = re.compile(r'^[\t ]+', re.MULTILINE)
re_preserve_spaces = re.compile(r'''
    [ ]           # space
    (?=[ ])       # lookahead for a space
    ''', re.VERBOSE)
re_angle_bracket_open = re.compile('<')
re_angle_bracket_close = re.compile('>')
md_chars_matcher_all = re.compile(r"([`\*_{}\[\]\(\)#!\\\.+-])")


def make_safe_path_portion(ustr, relaxed=True):
    """Return an ascii representation of ``ustr`` that conforms to mount point
    naming :attr:`rules <re_tool_mount_point_fragment>`.

    Will return an empty string if no char in ``ustr`` is latin1-encodable.

    :param relaxed: Use relaxed mount point naming rules (allows more
        characters. See :attr:`re_relaxed_tool_mount_point_fragment`.
    :returns: The converted string.

    """
    regex = (re_relaxed_tool_mount_point_fragment if relaxed else
             re_tool_mount_point_fragment)
    ustr = really_unicode(ustr)
    s = ustr.encode('latin1', 'ignore')
    s = AsciiDammit.asciiDammit(s)
    if not relaxed:
        s = s.lower()
    s = '-'.join(regex.findall(s))
    s = s.replace('--', '-')
    return s

def escape_json(data):
    return json.dumps(data).replace('<', '\\u003C')

def monkeypatch(*objs):
    def patchem(func):
        for obj in objs:
            setattr(obj, func.__name__, func)
    return patchem


def urlquote(url, safe="/"):
    try:
        return urllib.parse.quote(str(url), safe=safe)
    except UnicodeEncodeError:
        return urllib.parse.quote(url.encode('utf-8'), safe=safe)


def urlquoteplus(url, safe=""):
    try:
        return urllib.parse.quote_plus(str(url), safe=safe)
    except UnicodeEncodeError:
        return urllib.parse.quote_plus(url.encode('utf-8'), safe=safe)


def _attempt_encodings(s, encodings):
    if s is None:
        return ''
    for enc in encodings:
        try:
            if enc is None:
                return str(s)  # try default encoding
            else:
                return str(s, enc)
        except (UnicodeDecodeError, LookupError):
            pass
    # Return the repr of the str -- should always be safe
    return str(repr(str(s)))[1:-1]


def really_unicode(s):
    # Try to guess the encoding
    def encodings():
        yield None
        yield 'utf-8'
        yield chardet.detect(s[:1024])['encoding']
        yield chardet.detect(s)['encoding']
        yield 'latin-1'
    return _attempt_encodings(s, encodings())


def find_user(email):
    from allura import model as M
    return M.User.by_email_address(email)


def find_project(url_path):
    from allura import model as M
    for n in M.Neighborhood.query.find():
        if url_path.strip("/").startswith(n.url_prefix.strip("/")):
            break
    else:
        return None, url_path
    # easily off-by-one, might be better to join together everything but
    # url_prefix
    project_part = n.shortname_prefix + url_path[len(n.url_prefix):]
    parts = project_part.split('/')
    length = len(parts)
    while length:
        shortname = '/'.join(parts[:length])
        p = M.Project.query.get(shortname=shortname, deleted=False,
                                neighborhood_id=n._id)
        if p:
            return p, parts[length:]
        length -= 1
    return None, url_path.split('/')


def make_neighborhoods(ids):
    return _make_xs('Neighborhood', ids)


def make_projects(ids):
    return _make_xs('Project', ids)


def make_users(ids):
    return _make_xs('User', ids)


def make_roles(ids):
    return _make_xs('ProjectRole', ids)


def _make_xs(X, ids):
    from allura import model as M
    X = getattr(M, X)
    ids = list(ids)
    results = dict(
        (r._id, r)
        for r in X.query.find(dict(_id={'$in': ids})))
    result = (results.get(i) for i in ids)
    return (r for r in result if r is not None)


def make_app_admin_only(app):
    from allura.model.auth import ProjectRole
    admin_role = ProjectRole.by_name('Admin', app.project)
    for ace in [ace for ace in app.acl if ace.role_id != admin_role._id]:
        app.acl.remove(ace)


@contextmanager
def push_config(obj, **kw):
    saved_attrs = {}
    new_attrs = []
    for k, v in kw.items():
        try:
            saved_attrs[k] = getattr(obj, k)
        except AttributeError:
            new_attrs.append(k)
        setattr(obj, k, v)
    try:
        yield obj
    finally:
        for k, v in saved_attrs.items():
            setattr(obj, k, v)
        for k in new_attrs:
            delattr(obj, k)


def sharded_path(name, num_parts=2):
    parts = [
        name[:i + 1]
        for i in range(num_parts)]
    return '/'.join(parts)


def set_context(project_shortname_or_id, mount_point=None, app_config_id=None, neighborhood=None):
    from allura import model
    try:
        p = model.Project.query.get(_id=ObjectId(str(project_shortname_or_id)))
    except InvalidId:
        p = None
    if p is None and type(project_shortname_or_id) != ObjectId:
        if neighborhood is None:
            raise TypeError('neighborhood is required; it must not be None')
        if not isinstance(neighborhood, model.Neighborhood):
            n = model.Neighborhood.query.get(name=neighborhood)
            if n is None:
                try:
                    n = model.Neighborhood.query.get(
                        _id=ObjectId(str(neighborhood)))
                except InvalidId:
                    pass
            if n is None:
                raise exc.NoSuchNeighborhoodError(
                    "Couldn't find neighborhood %s" %
                    repr(neighborhood))
            neighborhood = n

        query = dict(shortname=project_shortname_or_id,
                     neighborhood_id=neighborhood._id)
        p = model.Project.query.get(**query)
    if p is None:
        raise exc.NoSuchProjectError("Couldn't find project %s nbhd %s" %
                                     (project_shortname_or_id, neighborhood))
    c.project = p

    if app_config_id is None:
        c.app = p.app_instance(mount_point)
    else:
        if isinstance(app_config_id, str):
            app_config_id = ObjectId(app_config_id)
        app_config = model.AppConfig.query.get(_id=app_config_id)
        c.app = p.app_instance(app_config)


@contextmanager
def push_context(project_id, mount_point=None, app_config_id=None, neighborhood=None):
    project = getattr(c, 'project', ())
    app = getattr(c, 'app', ())
    set_context(project_id, mount_point, app_config_id, neighborhood)
    try:
        yield
    finally:
        if project == ():
            del c.project
        else:
            c.project = project
        if app == ():
            del c.app
        else:
            c.app = app


def encode_keys(d):
    '''Encodes the unicode keys of d, making the result
    a valid kwargs argument'''
    return dict(
        (k.encode('utf-8'), v)
        for k, v in d.items())


def vardec(fun):
    def vardec_hook(remainder, params):
        new_params = variable_decode(dict(
            (k, v) for k, v in list(params.items())
            if re_clean_vardec_key.match(k)))
        params.update(new_params)
    before_validate(vardec_hook)(fun)
    return fun


def nonce(length=4):
    return sha1(ObjectId().binary + os.urandom(10)).hexdigest()[:length]


def cryptographic_nonce(length=40):
    hex_format = '%.2x' * length
    return hex_format % tuple(map(ord, os.urandom(length)))


def random_password(length=20, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for x in range(length))


def ago(start_time, show_date_after=7):
    """
    Return time since starting time as a rounded, human readable string.
    E.g., "3 hours ago"
    """

    if start_time is None:
        return 'unknown'
    granularities = ['century', 'decade', 'year', 'month', 'day', 'hour',
                     'minute']
    end_time = datetime.utcnow()
    if show_date_after is not None and end_time - start_time > timedelta(days=show_date_after):
        return start_time.strftime('%Y-%m-%d')

    while True:
        granularity = granularities.pop()
        ago = date.distance_of_time_in_words(
            start_time, end_time, granularity, round=True)
        rounded_to_one_granularity = 'and' not in ago
        if rounded_to_one_granularity:
            break
    return ago + ' ago'


def ago_ts(timestamp):
    return ago(datetime.utcfromtimestamp(timestamp))


def ago_string(s):
    try:
        return ago(parse(s, ignoretz=True))
    except (ValueError, AttributeError):
        return 'unknown'


class DateTimeConverter(FancyValidator):

    def _to_python(self, value, state):
        try:
            return parse(value)
        except ValueError:
            if self.if_invalid != formencode.api.NoDefault:
                return self.if_invalid
            else:
                raise

    def _from_python(self, value, state):
        return value.isoformat()


def absurl(url):
    """
    Given a root-relative URL, return a full URL including protocol and host
    """
    if url is None:
        return None
    if '://' in url:
        return url
    try:
        # try request first, so we can get proper http/https value
        host = request.host_url
    except TypeError:
        # for tests, etc
        host = tg.config['base_url'].rstrip('/')
    return host + url


def diff_text(t1, t2, differ=None):
    t1_lines = t1.replace('\r', '').split('\n')
    t2_lines = t2.replace('\r', '').split('\n')
    t1_words = []
    for line in t1_lines:
        for word in line.split(' '):
            t1_words.append(word)
        t1_words.append('\n')
    t2_words = []
    for line in t2_lines:
        for word in line.split(' '):
            t2_words.append(word)
        t2_words.append('\n')
    if differ is None:
        differ = difflib.SequenceMatcher(None, t1_words, t2_words)
    result = []
    for tag, i1, i2, j1, j2 in differ.get_opcodes():
        if tag in ('delete', 'replace'):
            result += ['<del>'] + t1_words[i1:i2] + ['</del>']
        if tag in ('insert', 'replace'):
            result += ['<ins>'] + t2_words[j1:j2] + ['</ins>']
        if tag == 'equal':
            result += t1_words[i1:i2]
    return ' '.join(result).replace('\n', '<br/>\n')


def gen_message_id(_id=None):
    if not _id:
        _id = nonce(40)
    if getattr(c, 'project', None):
        parts = c.project.url().split('/')[1:-1]
    else:
        parts = ['mail']
    if getattr(c, 'app', None):
        addr = '%s.%s' % (_id, c.app.config.options['mount_point'])
    else:
        addr = _id
    return '%s@%s.%s' % (
        addr, '.'.join(reversed(parts)), tg.config['domain'])


class ProxiedAttrMeta(type):

    def __init__(cls, name, bases, dct):
        for v in dct.values():
            if isinstance(v, attrproxy):
                v.cls = cls


class attrproxy(object):
    cls = None

    def __init__(self, *attrs):
        self.attrs = attrs

    def __repr__(self):
        return '<attrproxy on %s for %s>' % (
            self.cls, self.attrs)

    def __get__(self, obj, klass=None):
        if obj is None:
            obj = klass
        for a in self.attrs:
            obj = getattr(obj, a)
        return proxy(obj)

    def __getattr__(self, name):
        if self.cls is None:
            return promised_attrproxy(lambda: self.cls, name)
        return getattr(
            attrproxy(self.cls, *self.attrs),
            name)


class promised_attrproxy(attrproxy):

    def __init__(self, promise, *attrs):
        super(promised_attrproxy, self).__init__(*attrs)
        self._promise = promise

    def __repr__(self):
        return '<promised_attrproxy for %s>' % (self.attrs,)

    def __getattr__(self, name):
        cls = self._promise()
        return getattr(cls, name)


class proxy(object):

    def __init__(self, obj):
        self._obj = obj

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def __call__(self, *args, **kwargs):
        return self._obj(*args, **kwargs)


def render_genshi_plaintext(template_name, **template_vars):
    assert os.path.exists(template_name)
    fd = open(template_name)
    try:
        tpl_text = fd.read()
    finally:
        fd.close()
    filepath = os.path.dirname(template_name)
    tt = genshi.template.NewTextTemplate(tpl_text,
                                         filepath=filepath, filename=template_name)
    stream = tt.generate(**template_vars)
    return stream.render(encoding='utf-8').decode('utf-8')


@tg.expose(content_type='text/plain')
def json_validation_error(controller, **kwargs):
    result = dict(status='Validation Error',
                  errors=c.validation_exception.unpack_errors(),
                  value=c.validation_exception.value,
                  params=kwargs)
    response.status = 400
    return json.dumps(result, indent=2)


def pop_user_notifications(user=None):
    from allura import model as M
    if user is None:
        user = c.user
    mbox = M.Mailbox.query.get(user_id=user._id, is_flash=True)
    if mbox:
        notifications = M.Notification.query.find(
            dict(_id={'$in': mbox.queue}))
        mbox.queue = []
        mbox.queue_empty = True
        for n in notifications:
            # clean it up so it doesn't hang around
            M.Notification.query.remove({'_id': n._id})
            yield n


def config_with_prefix(d, prefix):
    '''Return a subdictionary keys with a given prefix,
    with the prefix stripped
    '''
    plen = len(prefix)
    return dict((k[plen:], v) for k, v in d.items()
                if k.startswith(prefix))


@contextmanager
def twophase_transaction(*engines):
    connections = [
        e.contextual_connect()
        for e in engines]
    txns = []
    to_rollback = []
    try:
        for conn in connections:
            txn = conn.begin_twophase()
            txns.append(txn)
            to_rollback.append(txn)
        yield
        to_rollback = []
        for txn in txns:
            txn.prepare()
            to_rollback.append(txn)
        for txn in txns:
            txn.commit()
    except:
        for txn in to_rollback:
            txn.rollback()
        raise


class log_action(object):
    extra_proto = dict(
        action=None,
        action_type=None,
        tool_type=None,
        tool_mount=None,
        project=None,
        neighborhood=None,
        username=None,
        url=None,
        ip_address=None)

    def __init__(self, logger, action):
        self._logger = logger
        self._action = action

    def log(self, level, message, *args, **kwargs):
        kwargs = dict(kwargs)
        extra = kwargs.setdefault('extra', {})
        meta = kwargs.pop('meta', {})
        kwpairs = extra.setdefault('kwpairs', {})
        for k, v in meta.items():
            kwpairs['meta_%s' % k] = v
        extra.update(self._make_extra())
        self._logger.log(level, self._action + ': ' + message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        self.log(logging.INFO, message, *args, **kwargs)

    def debug(self, message, *args, **kwargs):
        self.log(logging.DEBUG, message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self.log(logging.ERROR, message, *args, **kwargs)

    def critical(self, message, *args, **kwargs):
        self.log(logging.CRITICAL, message, *args, **kwargs)

    def exception(self, message, *args, **kwargs):
        self.log(logging.EXCEPTION, message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        self.log(logging.EXCEPTION, message, *args, **kwargs)
    warn = warning

    def _make_extra(self):
        result = dict(self.extra_proto, action=self._action)
        try:
            if hasattr(c, 'app') and c.app:
                result['tool_type'] = c.app.config.tool_name
                result['tool_mount'] = c.app.config.options['mount_point']
            if hasattr(c, 'project') and c.project:
                result['project'] = c.project.shortname
                result['neighborhood'] = c.project.neighborhood.name
            if hasattr(c, 'user') and c.user:
                result['username'] = c.user.username
            else:
                result['username'] = '*system'
            try:
                result['url'] = request.url
                result['ip_address'] = utils.ip_address(request)
            except TypeError:
                pass
            return result
        except:
            self._logger.warning(
                'Error logging to rtstats, some info may be missing', exc_info=True)
            return result


def paging_sanitizer(limit, page, total_count, zero_based_pages=True):
    """Return limit, page - both converted to int and constrained to
    valid ranges based on total_count.

    Useful for sanitizing limit and page query params.
    """
    limit = max(int(limit), 1)
    max_page = (total_count / limit) + (1 if total_count % limit else 0)
    max_page = max(0, max_page - (1 if zero_based_pages else 0))
    page = min(max(int(page), (0 if zero_based_pages else 1)), max_page)
    return limit, page


def _add_inline_line_numbers_to_text(text):
    markup_text = '<div class="codehilite"><pre>'
    for line_num, line in enumerate(text.splitlines(), 1):
        markup_text = markup_text + \
            '<span id="l%s" class="code_block"><span class="lineno">%s</span> %s</span>' % (
                line_num, line_num, line)
    markup_text = markup_text + '</pre></div>'
    return markup_text


def _add_table_line_numbers_to_text(text):
    def _prepend_whitespaces(num, max_num):
        num, max_num = str(num), str(max_num)
        diff = len(max_num) - len(num)
        return ' ' * diff + num

    def _len_to_str_column(l, start=1):
        max_num = l + start
        return '\n'.join(map(_prepend_whitespaces, list(range(start, max_num)), [max_num] * l))

    lines = text.splitlines(True)
    linenumbers = '<td class="linenos"><div class="linenodiv"><pre>' + \
        _len_to_str_column(len(lines)) + '</pre></div></td>'
    markup_text = '<table class="codehilitetable"><tbody><tr>' + \
        linenumbers + '<td class="code"><div class="codehilite"><pre>'
    for line_num, line in enumerate(lines, 1):
        markup_text = markup_text + \
            '<span id="l%s" class="code_block">%s</span>' % (line_num, line)
    markup_text = markup_text + '</pre></div></td></tr></tbody></table>'
    return markup_text


INLINE = 'inline'
TABLE = 'table'


def render_any_markup(name, text, code_mode=False, linenumbers_style=TABLE):
    """
    renders markdown using allura enhacements if file is in markdown format
    renders any other markup format using the pypeline
    Returns jinja-safe text
    """
    if text == '':
        text = '<p><em>Empty File</em></p>'
    else:
        fmt = g.pypeline_markup.can_render(name)
        if fmt == 'markdown':
            text = g.markdown.convert(text)
        else:
            text = g.pypeline_markup.render(name, text)
        if not fmt:
            if code_mode and linenumbers_style == INLINE:
                text = _add_inline_line_numbers_to_text(text)
            elif code_mode and linenumbers_style == TABLE:
                text = _add_table_line_numbers_to_text(text)
            else:
                text = '<pre>%s</pre>' % text
    return Markup(text)

# copied from jinja2 dev
# latest release, 2.6, implements this incorrectly
# can remove and use jinja2 implementation after upgrading to 2.7


def do_filesizeformat(value, binary=False):
    """Format the value like a 'human-readable' file size (i.e. 13 kB,
4.1 MB, 102 Bytes, etc). Per default decimal prefixes are used (Mega,
Giga, etc.), if the second parameter is set to `True` the binary
prefixes are used (Mebi, Gibi).
"""
    bytes = float(value)
    base = binary and 1024 or 1000
    prefixes = [
        (binary and 'KiB' or 'kB'),
        (binary and 'MiB' or 'MB'),
        (binary and 'GiB' or 'GB'),
        (binary and 'TiB' or 'TB'),
        (binary and 'PiB' or 'PB'),
        (binary and 'EiB' or 'EB'),
        (binary and 'ZiB' or 'ZB'),
        (binary and 'YiB' or 'YB')
    ]
    if bytes == 1:
        return '1 Byte'
    elif bytes < base:
        return '%d Bytes' % bytes
    else:
        for i, prefix in enumerate(prefixes):
            unit = base ** (i + 2)
            if bytes < unit:
                return '%.1f %s' % ((base * bytes / unit), prefix)
        return '%.1f %s' % ((base * bytes / unit), prefix)


def nl2br_jinja_filter(value):
    result = '<br>\n'.join(escape(line) for line in value.split('\n'))
    return Markup(result)


def log_if_changed(artifact, attr, new_val, message):
    """Set `artifact.attr` to `new_val` if changed. Add AuditLog record."""
    from allura import model as M
    if not hasattr(artifact, attr):
        return
    if getattr(artifact, attr) != new_val:
        M.AuditLog.log(message)
        setattr(artifact, attr, new_val)


def get_tool_packages(tool_name):
    "Return package for given tool (e.g. 'forgetracker' for 'tickets')"
    from allura.app import Application
    app = g.entry_points['tool'].get(tool_name.lower())
    if not app:
        return []
    classes = [c for c in app.mro() if c not in (Application, object)]
    return [cls.__module__.split('.')[0] for cls in classes]


def get_first(d, key):
    """Return value for d[key][0] if d[key] is a list with elements, else return d[key].

    Useful to retrieve values from solr index (e.g. `title` and `text` fields),
    which are stored as lists.
    """
    v = d.get(key)
    if isinstance(v, list):
        return v[0] if len(v) > 0 else None
    return v


def datetimeformat(value, format='%Y-%m-%d %H:%M:%S'):
    return value.strftime(format)


@contextmanager
def log_output(log):
    class Writer(object):

        def __init__(self, func):
            self.func = func
            self.closed = False

        def write(self, buf):
            self.func(buf)

        def flush(self):
            pass

    _stdout = sys.stdout
    _stderr = sys.stderr
    sys.stdout = Writer(log.info)
    sys.stderr = Writer(log.error)
    try:
        yield log
    finally:
        sys.stdout = _stdout
        sys.stderr = _stderr


def topological_sort(items, partial_order):
    """Perform topological sort.
       items is a list of items to be sorted.
       partial_order is a list of pairs. If pair (a,b) is in it, it means
       that item a should appear before item b.
       Returns a list of the items in one of the possible orders, or None
       if partial_order contains a loop.

       Modified from: http://www.bitformation.com/art/python_toposort.html
    """
    # Original topological sort code written by Ofer Faigon
    # (www.bitformation.com) and used with permission

    def add_arc(graph, fromnode, tonode):
        """Add an arc to a graph. Can create multiple arcs.
           The end nodes must already exist."""
        graph[fromnode].append(tonode)
        # Update the count of incoming arcs in tonode.
        graph[tonode][0] = graph[tonode][0] + 1

    # step 1 - create a directed graph with an arc a->b for each input
    # pair (a,b).
    # The graph is represented by a dictionary. The dictionary contains
    # a pair item:list for each node in the graph. /item/ is the value
    # of the node. /list/'s 1st item is the count of incoming arcs, and
    # the rest are the destinations of the outgoing arcs. For example:
    #           {'a':[0,'b','c'], 'b':[1], 'c':[1]}
    # represents the graph:   c <-- a --> b
    # The graph may contain loops and multiple arcs.
    # Note that our representation does not contain reference loops to
    # cause GC problems even when the represented graph contains loops,
    # because we keep the node names rather than references to the nodes.
    graph = defaultdict(lambda: [0])
    for a, b in partial_order:
        add_arc(graph, a, b)

    # Step 2 - find all roots (nodes with zero incoming arcs).
    roots = [n for n in items if graph[n][0] == 0]
    roots.reverse()  # keep sort stable

    # step 3 - repeatedly emit a root and remove it from the graph. Removing
    # a node may convert some of the node's direct children into roots.
    # Whenever that happens, we append the new roots to the list of
    # current roots.
    sorted = []
    while roots:
        # If len(roots) is always 1 when we get here, it means that
        # the input describes a complete ordering and there is only
        # one possible output.
        # When len(roots) > 1, we can choose any root to send to the
        # output; this freedom represents the multiple complete orderings
        # that satisfy the input restrictions. We arbitrarily take one of
        # the roots using pop(). Note that for the algorithm to be efficient,
        # this operation must be done in O(1) time.
        root = roots.pop()
        sorted.append(root)
        for child in graph[root][1:]:
            graph[child][0] = graph[child][0] - 1
            if graph[child][0] == 0:
                roots.append(child)
        del graph[root]
    if len(graph) > 0:
        # There is a loop in the input.
        return None
    return sorted


@contextmanager
def ming_config(**conf):
    """Temporarily swap in a new ming configuration, restoring the previous
    one when the contextmanager exits.

    :param \*\*conf: keyword arguments defining the new ming configuration

    """
    import ming
    from ming.session import Session
    datastores = Session._datastores
    try:
        ming.configure(**conf)
        yield
    finally:
        Session._datastores = datastores
        for name, session in Session._registry.items():
            session.bind = datastores.get(name, None)
            session._name = name


@contextmanager
def ming_config_from_ini(ini_path):
    """Temporarily swap in a new ming configuration, restoring the previous
    one when the contextmanager exits.

    :param ini_path: Path to ini file containing the ming configuration

    """
    root = pkg_resources.get_distribution('allura').location
    conf = appconfig('config:%s' % os.path.join(root, ini_path))
    with ming_config(**conf):
        yield


def split_select_field_options(field_options):
    try:
        # shlex have problems with parsing unicode,
        # it's better to pass properly encoded byte-string
        field_options = shlex.split(field_options.encode('utf-8'))
        # convert splitted string back to unicode
        field_options = list(map(really_unicode, field_options))
    except ValueError:
        field_options = field_options.split()
        # After regular split field_options might contain a " characters,
        # which would break html when rendered inside tag's value attr.
        # Escaping doesn't help here, 'cause it breaks EasyWidgets' validation,
        # so we're getting rid of those.
        field_options = [o.replace('"', '') for o in field_options]
    return field_options


@contextmanager
def notifications_disabled(project, disabled=True):
    """Temporarily disable email notifications on a project.

    """
    orig = project.notifications_disabled
    try:
        project.notifications_disabled = disabled
        yield
    finally:
        project.notifications_disabled = orig


@contextmanager
def null_contextmanager(*args, **kw):
    """A no-op contextmanager.

    """
    yield


class exceptionless(object):

    '''Decorator making the decorated function return 'error_result' on any
    exceptions rather than propagating exceptions up the stack
    '''

    def __init__(self, error_result, log=None):
        self.error_result = error_result
        self.log = log

    def __call__(self, fun):
        fname = 'exceptionless(%s)' % fun.__name__

        def inner(*args, **kwargs):
            try:
                return fun(*args, **kwargs)
            except Exception as e:
                if self.log:
                    self.log.exception(
                        'Error calling %s(args=%s, kwargs=%s): %s',
                        fname, args, kwargs, str(e))
                return self.error_result
        inner.__name__ = fname
        return inner


def urlopen(url, retries=3, codes=(408,), timeout=None):
    """Open url, optionally retrying if an error is encountered.

    Socket timeouts will always be retried if retries > 0.
    HTTP errors are retried if the error code is passed in ``codes``.

    :param retries: Number of time to retry.
    :param codes: HTTP error codes that should be retried.

    """
    attempts = 0
    while True:
        try:
            return urllib.request.urlopen(url, timeout=timeout)
        except (urllib.error.HTTPError, socket.timeout) as e:
            if attempts < retries and (isinstance(e, socket.timeout) or
                                       e.code in codes):
                attempts += 1
                continue
            else:
                try:
                    url_string = url.get_full_url()  # if url is Request obj
                except Exception:
                    url_string = url
                if timeout is None:
                    timeout = socket.getdefaulttimeout()
                log.exception(
                    'Failed after %s retries on url with a timeout of %s: %s: %s',
                    attempts, timeout, url_string, e)
                raise e


def plain2markdown(text, preserve_multiple_spaces=False, has_html_entities=False):
    if not has_html_entities:
        # prevent &foo; and &#123; from becoming HTML entities
        text = re_amp.sub('&amp;', text)
    # avoid accidental 4-space indentations creating code blocks
    if preserve_multiple_spaces:
        text = text.replace('\t', ' ' * 4)
        text = re_preserve_spaces.sub('&nbsp;', text)
    else:
        text = re_leading_spaces.sub('', text)
    try:
        # try to use html2text for most of the escaping
        import html2text
        html2text.BODY_WIDTH = 0
        text = html2text.escape_md_section(text, snob=True)
    except ImportError:
        # fall back to just escaping any MD-special chars
        text = md_chars_matcher_all.sub(r"\\\1", text)
    # prevent < and > from becoming tags
    text = re_angle_bracket_open.sub('&lt;', text)
    text = re_angle_bracket_close.sub('&gt;', text)
    return text


def iter_entry_points(group, *a, **kw):
    """Yields entry points that have not been disabled in the config.

    If ``group`` is "allura" (Allura tool entry points), this function also
    checks for multiple entry points with the same name. If there are
    multiple entry points with the same name, and one of them is a subclass
    of the other(s), it will be yielded, and the other entry points with that
    name will be ignored. If a subclass is not found, an ImportError will be
    raised.

    This treatment of "allura" entry points allows tool authors to subclass
    another tool while reusing the original entry point name.

    """
    def active_eps():
        disabled = aslist(
            tg.config.get('disable_entry_points.' + group), sep=',')
        return [ep for ep in pkg_resources.iter_entry_points(group, *a, **kw)
                if ep.name not in disabled]

    def unique_eps(entry_points):
        by_name = defaultdict(list)
        for ep in entry_points:
            by_name[ep.name].append(ep)
        for name, eps in by_name.items():
            ep_count = len(eps)
            if ep_count == 1:
                yield eps[0]
            else:
                yield subclass(eps)

    def subclass(entry_points):
        loaded = dict((ep, ep.load()) for ep in entry_points)
        for ep, cls in loaded.items():
            others = list(loaded.values())[:]
            others.remove(cls)
            if all([issubclass(cls, other) for other in others]):
                return ep
        raise ImportError('Ambiguous [allura] entry points detected. ' +
                          'Multiple entry points with name "%s".' % entry_points[0].name)
    return iter(unique_eps(active_eps()) if group == 'allura' else active_eps())


# http://stackoverflow.com/a/1060330/79697
def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days)):
        yield start_date + timedelta(n)


@contextmanager
def login_overlay(exceptions=None):
    """
    Override the default behavior of redirecting to the auth.login_url and
    instead display an overlay with content from auth.login_fragment_url.

    This is to allow pages that require authentication for any actions but
    not for the initial view to be more apparent what you will get once
    logged in.

    This should be wrapped around call to `require_access()` (presumably in
    the `_check_security()` method on a controller).  The `exceptions` param
    can be given a list of exposed views to leave with the original behavior.

    For example:

        class MyController(BaseController);
            def _check_security(self):
                with login_overlay(exceptions=['process']):
                    require_access(self.neighborhood, 'register')

            @expose
            def index(self, *args, **kw):
                return {}

            @expose
            def list(self, *args, **kw):
                return {}

            @expose
            def process(self, *args, **kw):
                return {}

    This would show the overlay to unauthenticated users who visit `/`
    or `/list` but would perform the normal redirect when `/process` is
    visited.
    """
    try:
        yield
    except HTTPUnauthorized as e:
        if exceptions:
            for exception in exceptions:
                if request.path.rstrip('/').endswith('/%s' % exception):
                    raise
        c.show_login_overlay = True


def get_filter(ctx, filter_name):
    """
    Gets a named Jinja2 filter, passing through
    any context requested by the filter.
    """
    filter_ = ctx.environment.filters[filter_name]
    if getattr(filter_, 'contextfilter', False):
        return partial(filter_, ctx)
    elif getattr(filter_, 'evalcontextfilter', False):
        return partial(filter_, ctx.eval_ctx)
    elif getattr(filter_, 'environmentfilter', False):
        return partial(filter_, ctx.environment)


@contextfilter
def map_jinja_filter(ctx, seq, filter_name, *a, **kw):
    """
    A Jinja2 filter that applies the named filter with the
    given args to the sequence this filter is applied to.
    """
    filter_ = get_filter(ctx, filter_name)
    return [filter_(value, *a, **kw) for value in seq]


def unidiff(old, new):
    """Returns unified diff between `one` and `two`."""
    return '\n'.join(difflib.unified_diff(
        a=old.splitlines(),
        b=new.splitlines(),
        fromfile='old',
        tofile='new',
        lineterm=''))


def auditlog_user(message, *args, **kwargs):
    """
    Create an audit log entry for a user, including the IP address

    :param str message:
    :param user: a :class:`allura.model.auth.User`
    """
    from allura import model as M
    ip_address = utils.ip_address(request)
    message = 'IP Address: {}\n'.format(ip_address) + message
    if kwargs.get('user') and kwargs['user'] != c.user:
        message = 'Done by user: {}\n'.format(c.user.username) + message
    return M.AuditLog.log_user(message, *args, **kwargs)


def get_user_status(user):
    '''
    Get user status based on disabled and pending attrs

    :param user: a :class:`allura.model.auth.User`
    '''
    disabled = user.disabled
    pending = user.pending

    if not disabled and not pending:
        return 'enabled'
    elif disabled:
        return 'disabled'
    elif pending:
        return 'pending'