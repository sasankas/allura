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
import re
import os
import allura
import unittest
import hashlib
import datetime as dt
from mock import patch, Mock

from bson import ObjectId
from nose.tools import with_setup, assert_equal, assert_in, assert_not_in
from pylons import tmpl_context as c, app_globals as g
import tg

from ming.orm import ThreadLocalORMSession
from alluratest.controller import (
    setup_basic_test,
    setup_global_objects,
    setup_unit_test,
    setup_functional_test,
    setup_trove_categories,
)

from allura import model as M
from allura.lib import helpers as h
from allura.lib.app_globals import ForgeMarkdown, NeighborhoodCache
from allura.tests import decorators as td

from forgewiki import model as WM
from forgeblog import model as BM


def setUp():
    """Method called by nose once before running the package.  Some functions need it run again to reset data"""
    setup_basic_test()
    setup_unit_test()
    setup_with_tools()


def tearDown():
    setUp()


@td.with_wiki
def setup_with_tools():
    setup_global_objects()


@td.with_wiki
def test_app_globals():
    with h.push_context('test', 'wiki', neighborhood='Projects'):
        assert g.app_static(
            'css/wiki.css') == '/nf/_static_/wiki/css/wiki.css', g.app_static('css/wiki.css')
        assert g.url(
            '/foo', a='foo bar') == 'http://localhost/foo?a=foo+bar', g.url('/foo', a='foo bar')
        assert g.url('/foo') == 'http://localhost/foo', g.url('/foo')


@with_setup(setUp)
def test_macro_projects():
    file_name = 'neo-icon-set-454545-256x350.png'
    file_path = os.path.join(
        allura.__path__[0], 'nf', 'allura', 'images', file_name)

    p_nbhd = M.Neighborhood.query.get(name='Projects')
    p_test = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
    c.project = p_test
    icon_file = open(file_path)
    M.ProjectFile.save_image(
        file_name, icon_file, content_type='image/png',
        square=True, thumbnail_size=(48, 48),
        thumbnail_meta=dict(project_id=c.project._id, category='icon'))
    icon_file.close()
    p_test2 = M.Project.query.get(
        shortname='test2', neighborhood_id=p_nbhd._id)
    c.project = p_test2
    icon_file = open(file_path)
    M.ProjectFile.save_image(
        file_name, icon_file, content_type='image/png',
        square=True, thumbnail_size=(48, 48),
        thumbnail_meta=dict(project_id=c.project._id, category='icon'))
    icon_file.close()
    p_sub1 = M.Project.query.get(
        shortname='test/sub1', neighborhood_id=p_nbhd._id)
    c.project = p_sub1
    icon_file = open(file_path)
    M.ProjectFile.save_image(
        file_name, icon_file, content_type='image/png',
        square=True, thumbnail_size=(48, 48),
        thumbnail_meta=dict(project_id=c.project._id, category='icon'))
    icon_file.close()
    p_test.labels = ['test', 'root']
    p_sub1.labels = ['test', 'sub1']
    # Make one project private
    p_test.private = False
    p_sub1.private = False
    p_test2.private = True

    ThreadLocalORMSession.flush_all()

    with h.push_config(c,
                       project=p_nbhd.neighborhood_project,
                       user=M.User.by_username('test-admin')):
        r = g.markdown_wiki.convert('[[projects]]')
        assert 'alt="Test Project Logo"' in r, r
        assert 'alt="A Subproject Logo"' in r, r
        r = g.markdown_wiki.convert('[[projects labels=root]]')
        assert 'alt="Test Project Logo"' in r, r
        assert 'alt="A Subproject Logo"' not in r, r
        r = g.markdown_wiki.convert('[[projects labels=sub1]]')
        assert 'alt="Test Project Logo"' not in r, r
        assert 'alt="A Subproject Logo"' in r, r
        r = g.markdown_wiki.convert('[[projects labels=test]]')
        assert 'alt="Test Project Logo"' in r, r
        assert 'alt="A Subproject Logo"' in r, r
        r = g.markdown_wiki.convert('[[projects labels=test,root]]')
        assert 'alt="Test Project Logo"' in r, r
        assert 'alt="A Subproject Logo"' not in r, r
        r = g.markdown_wiki.convert('[[projects labels=test,sub1]]')
        assert 'alt="Test Project Logo"' not in r, r
        assert 'alt="A Subproject Logo"' in r, r
        r = g.markdown_wiki.convert('[[projects labels=root|sub1]]')
        assert 'alt="Test Project Logo"' in r, r
        assert 'alt="A Subproject Logo"' in r, r
        r = g.markdown_wiki.convert('[[projects labels=test,root|root,sub1]]')
        assert 'alt="Test Project Logo"' in r, r
        assert 'alt="A Subproject Logo"' not in r, r
        r = g.markdown_wiki.convert('[[projects labels=test,root|test,sub1]]')
        assert 'alt="Test Project Logo"' in r, r
        assert 'alt="A Subproject Logo"' in r, r
        r = g.markdown_wiki.convert('[[projects show_total=True sort=random]]')
        assert '<p class="macro_projects_total">3 Projects' in r, r
        r = g.markdown_wiki.convert(
            '[[projects show_total=True private=True sort=random]]')
        assert '<p class="macro_projects_total">1 Projects' in r, r
        assert 'alt="Test 2 Logo"' in r, r
        assert 'alt="Test Project Logo"' not in r, r
        assert 'alt="A Subproject Logo"' not in r, r

        r = g.markdown_wiki.convert('[[projects show_proj_icon=True]]')
        assert 'alt="Test Project Logo"' in r
        r = g.markdown_wiki.convert('[[projects show_proj_icon=False]]')
        assert 'alt="Test Project Logo"' not in r


def test_macro_gittip_button():
    p_nbhd = M.Neighborhood.query.get(name='Projects')
    p_test = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
    with h.push_config(c, project=p_test):
        r = g.markdown_wiki.convert('[[gittip_button username=test]]')
    assert_equal(
        r, '<div class="markdown_content"><p><iframe height="22pt" src="https://www.gittip.com/test/widget.html" style="border: 0; margin: 0; padding: 0;" width="48pt"></iframe>\n</p></div>')


def test_macro_neighborhood_feeds():
    p_nbhd = M.Neighborhood.query.get(name='Projects')
    p_test = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
    with h.push_context('--init--', 'wiki', neighborhood='Projects'):
        r = g.markdown_wiki.convert('[[neighborhood_feeds tool_name=wiki]]')
        assert 'Home modified by' in r, r
        orig_len = len(r)
        # Make project private & verify we don't see its new feed items
        anon = M.User.anonymous()
        p_test.acl.insert(0, M.ACE.deny(
            M.ProjectRole.anonymous(p_test)._id, 'read'))
        ThreadLocalORMSession.flush_all()
        pg = WM.Page.query.get(title='Home', app_config_id=c.app.config._id)
        pg.text = 'Change'
        with h.push_config(c, user=M.User.by_username('test-admin')):
            pg.commit()
        r = g.markdown_wiki.convert('[[neighborhood_feeds tool_name=wiki]]')
        new_len = len(r)
        assert new_len == orig_len
        p = BM.BlogPost(title='test me',
                        neighborhood_id=p_test.neighborhood_id)
        p.text = 'test content'
        p.state = 'published'
        p.make_slug()
        with h.push_config(c, user=M.User.by_username('test-admin')):
            p.commit()
        ThreadLocalORMSession.flush_all()
        with h.push_config(c, user=anon):
            r = g.markdown_wiki.convert('[[neighborhood_blog_posts]]')
        assert 'test content' in r


@with_setup(setUp)
def test_macro_members():
    p_nbhd = M.Neighborhood.query.get(name='Projects')
    p_test = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
    p_test.add_user(M.User.by_username('test-user'), ['Developer'])
    p_test.add_user(M.User.by_username('test-user-0'), ['Member'])
    ThreadLocalORMSession.flush_all()
    r = g.markdown_wiki.convert('[[members limit=2]]')
    assert_equal(r, '<div class="markdown_content"><h6>Project Members:</h6>\n'
                 '<ul class="md-users-list">\n'
                 '<li><a href="/u/test-admin">Test Admin</a> (admin)</li><li><a href="/u/test-user">Test User</a></li>\n'
                 '<li class="md-users-list-more"><a href="/p/test/_members">All Members</a></li>\n'
                 '</ul>\n'
                 '</div>')


@with_setup(setUp)
def test_macro_members_escaping():
    user = M.User.by_username('test-admin')
    user.display_name = 'Test Admin <script>'
    r = g.markdown_wiki.convert('[[members]]')
    assert_equal(r, '<div class="markdown_content"><h6>Project Members:</h6>\n'
                 '<ul class="md-users-list">\n'
                 '<li><a href="/u/test-admin">Test Admin &lt;script&gt;</a> (admin)</li>\n'
                 '</ul>\n</div>')


@with_setup(setUp)
def test_macro_project_admins():
    user = M.User.by_username('test-admin')
    user.display_name = 'Test Ådmin <script>'
    with h.push_context('test', neighborhood='Projects'):
        r = g.markdown_wiki.convert('[[project_admins]]')
    assert_equal(
        r, '<div class="markdown_content"><h6>Project Admins:</h6>\n<ul class="md-users-list">\n<li><a href="/u/test-admin">Test \xc5dmin &lt;script&gt;</a></li>\n</ul>\n</div>')


@with_setup(setUp)
def test_macro_project_admins_one_br():
    p_nbhd = M.Neighborhood.query.get(name='Projects')
    p_test = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
    p_test.add_user(M.User.by_username('test-user'), ['Admin'])
    ThreadLocalORMSession.flush_all()
    with h.push_config(c, project=p_test):
        r = g.markdown_wiki.convert('[[project_admins]]\n[[download_button]]')

    assert not '</a><br /><br /><a href=' in r, r
    assert '</a></li><li><a href=' in r, r


@td.with_wiki
def test_macro_include_no_extra_br():
    p_nbhd = M.Neighborhood.query.get(name='Projects')
    p_test = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
    wiki = p_test.app_instance('wiki')
    with h.push_context(p_test._id, app_config_id=wiki.config._id):
        p = WM.Page.upsert(title='Include_1')
        p.text = 'included page 1'
        p.commit()
        p = WM.Page.upsert(title='Include_2')
        p.text = 'included page 2'
        p.commit()
        p = WM.Page.upsert(title='Include_3')
        p.text = 'included page 3'
        p.commit()
        ThreadLocalORMSession.flush_all()
        md = '[[include ref=Include_1]]\n[[include ref=Include_2]]\n[[include ref=Include_3]]'
        html = g.markdown_wiki.convert(md)

    expected_html = '''
<div class="markdown_content">
<p>
<div><div class="markdown_content"><p>included page 1</p></div></div>
<div><div class="markdown_content"><p>included page 2</p></div></div>
<div><div class="markdown_content"><p>included page 3</p></div></div>
</p>
<p></p>
</div>
'''.strip().replace('\n', '')
    assert html.strip().replace('\n', '') == expected_html, html

@with_setup(setUp, tearDown)
@td.with_wiki
@td.with_tool('test', 'Wiki', 'wiki2')
def test_macro_include_permissions():
    p_nbhd = M.Neighborhood.query.get(name='Projects')
    p_test = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
    wiki = p_test.app_instance('wiki')
    wiki2 = p_test.app_instance('wiki2')
    with h.push_context(p_test._id, app_config_id=wiki.config._id):
        p = WM.Page.upsert(title='CanRead')
        p.text = 'Can see this!'
        p.commit()
        ThreadLocalORMSession.flush_all()

    with h.push_context(p_test._id, app_config_id=wiki2.config._id):
        role = M.ProjectRole.by_name('*anonymous')._id
        read_perm = M.ACE.allow(role, 'read')
        acl = c.app.config.acl
        if read_perm in acl:
            acl.remove(read_perm)
        p = WM.Page.upsert(title='CanNotRead')
        p.text = 'Can not see this!'
        p.commit()
        ThreadLocalORMSession.flush_all()

    with h.push_context(p_test._id, app_config_id=wiki.config._id):
        c.user = M.User.anonymous()
        md = '[[include ref=CanRead]]\n[[include ref=wiki2:CanNotRead]]'
        html = g.markdown_wiki.convert(md)
        assert_in('Can see this!', html)
        assert_not_in('Can not see this!', html)
        assert_in("[[include: you don't have a read permission for wiki2:CanNotRead]]", html)


@patch('oembed.OEmbedEndpoint.fetch')
def test_macro_embed(oembed_fetch):
    oembed_fetch.return_value = {
        "html": '<iframe width="480" height="270" src="http://www.youtube.com/embed/kOLpSPEA72U?feature=oembed" frameborder="0" allowfullscreen></iframe>)',
        "title": "Nature's 3D Printer: MIND BLOWING Cocoon in Rainforest - Smarter Every Day 94",
    }
    r = g.markdown_wiki.convert(
        '[[embed url=http://www.youtube.com/watch?v=kOLpSPEA72U]]')
    assert_in('<div class="grid-20"><iframe height="270" src="https://www.youtube.com/embed/kOLpSPEA72U?feature=oembed" width="480"></iframe>\n</div>',
              r)


def test_macro_embed_notsupported():
    r = g.markdown_wiki.convert('[[embed url=http://vimeo.com/46163090]]')
    assert_equal(
        r, '<div class="markdown_content"><p>[[embed url=http://vimeo.com/46163090]]</p></div>')


def test_markdown_toc():
    with h.push_context('test', neighborhood='Projects'):
        r = g.markdown_wiki.convert("""[TOC]

# Header 1

## Header 2""")
    assert '''<ul>
<li><a href="#header-1">Header 1</a><ul>
<li><a href="#header-2">Header 2</a></li>
</ul>
</li>
</ul>''' in r, r


@td.with_wiki
def test_wiki_artifact_links():
    text = g.markdown.convert('See [18:13:49]')
    assert 'See <span>[18:13:49]</span>' in text, text
    with h.push_context('test', 'wiki', neighborhood='Projects'):
        text = g.markdown.convert('Read [here](Home) about our project')
        assert '<a class="" href="/p/test/wiki/Home">here</a>' in text, text
        text = g.markdown.convert('[Go home](test:wiki:Home)')
        assert '<a class="" href="/p/test/wiki/Home">Go home</a>' in text, text
        text = g.markdown.convert('See [test:wiki:Home]')
        assert '<a class="alink" href="/p/test/wiki/Home">[test:wiki:Home]</a>' in text, text


def test_markdown_links():
    with patch.dict(tg.config, {'nofollow_exempt_domains': 'foobar.net'}):
        text = g.markdown.convert(
            'Read [here](http://foobar.net/) about our project')
        assert_in('class="" href="http://foobar.net">here</a> about', text)

    text = g.markdown.convert(
        'Read [here](http://foobar.net/) about our project')
    assert_in('class="" href="http://foobar.net" rel="nofollow">here</a> about', text)

    text = g.markdown.convert('Read [here](/p/foobar/blah) about our project')
    assert_in('class="" href="/p/foobar/blah">here</a> about', text)

    text = g.markdown.convert('Read <http://foobar.net/> about our project')
    assert_in(
        'href="http://foobar.net" rel="nofollow">http://foobar.net/</a> about', text)


def test_markdown_and_html():
    with h.push_context('test', neighborhood='Projects'):
        r = g.markdown_wiki.convert('<div style="float:left">blah</div>')
    assert '<div style="float: left;">blah</div>' in r, r


def test_markdown_within_html():
    with h.push_context('test', neighborhood='Projects'):
        r = g.markdown_wiki.convert(
            '<div style="float:left" markdown>**blah**</div>')
    assert '''<div style="float: left;">
<p><strong>blah</strong></p>
</div>''' in r, r


def test_markdown_with_html_comments():
    text = g.markdown.convert('test <!-- comment -->')
    assert '<div class="markdown_content"><p>test </p></div>' == text, text


def test_markdown_big_text():
    '''If text is too big g.markdown.convert should return plain text'''
    text = 'a' * 40001
    assert_equal(g.markdown.convert(text), '<pre>%s</pre>' % text)
    assert_equal(g.markdown_wiki.convert(text), '<pre>%s</pre>' % text)


@td.with_wiki
def test_markdown_basics():
    with h.push_context('test', 'wiki', neighborhood='Projects'):
        text = g.markdown.convert('# Foo!\n[Home]')
        assert '<a class="alink" href="/p/test/wiki/Home">[Home]</a>' in text, text
        text = g.markdown.convert('# Foo!\n[Rooted]')
        assert '<a href=' not in text, text

    assert '<br' in g.markdown.convert(
        'Multi\nLine'), g.markdown.convert('Multi\nLine')
    assert '<br' not in g.markdown.convert('Multi\n\nLine')

    g.markdown.convert("<class 'foo'>")  # should not raise an exception
    assert '<br>' not in g.markdown.convert('''# Header

Some text in a regular paragraph

    :::python
    for i in range(10):
        print i
''')
    assert_in('http://localhost:8080/', g.forge_markdown(email=True).convert('[Home]'))
    assert 'class="codehilite"' in g.markdown.convert('''
~~~~
def foo(): pass
~~~~''')


def test_markdown_autolink():
    tgt = 'http://everything2.com/?node=nate+oostendorp'
    s = g.markdown.convert('This is %s' % tgt)
    assert_equal(
        s, '<div class="markdown_content"><p>This is <a href="%s" rel="nofollow">%s</a></p></div>' % (tgt, tgt))
    assert '<a href=' in g.markdown.convert('This is http://domain.net')
    # beginning of doc
    assert_in('<a href=', g.markdown.convert('http://domain.net abc'))
    # beginning of a line
    assert_in('<br />\n<a href="http://',
              g.markdown.convert('foobar\nhttp://domain.net abc'))
    # no conversion of these urls:
    assert_in('a blahttp://sdf.com z',
              g.markdown.convert('a blahttp://sdf.com z'))
    assert_in('literal <code>http://domain.net</code> literal',
              g.markdown.convert('literal `http://domain.net` literal'))
    assert_in('<pre>preformatted http://domain.net\n</pre>',
              g.markdown.convert('    :::text\n'
                                 '    preformatted http://domain.net'))


def test_markdown_autolink_with_escape():
    # \_ is unnecessary but valid markdown escaping and should be considered as a regular underscore
    # (it occurs during html2text conversion during project migrations)
    r = g.markdown.convert(
        'a http://www.phpmyadmin.net/home\_page/security/\#target b')
    assert 'href="http://www.phpmyadmin.net/home_page/security/#target"' in r, r


def test_markdown_invalid_script():
    r = g.markdown.convert('<script>alert(document.cookies)</script>')
    assert_equal('<div class="markdown_content">&lt;script&gt;alert(document.cookies)&lt;/script&gt;\n</div>', r)


def test_markdown_invalid_onerror():
    r = g.markdown.convert('<img src=x onerror=alert(document.cookie)>')
    assert_not_in('onerror', r)


def test_markdown_invalid_tagslash():
    r = g.markdown.convert('<div/onload><img src=x onerror=alert(document.cookie)>')
    assert_not_in('onerror', r)


@td.with_wiki
def test_macro_include():
    r = g.markdown.convert('[[include ref=Home id=foo]]')
    assert '<div id="foo">' in r, r
    assert 'href="../foo"' in g.markdown.convert('[My foo](foo)')
    assert 'href="..' not in g.markdown.convert('[My foo](./foo)')


def test_macro_nbhd_feeds():
    with h.push_context('--init--', 'wiki', neighborhood='Projects'):
        r = g.markdown_wiki.convert('[[neighborhood_feeds tool_name=wiki]]')
        assert 'Home modified by ' in r, r
        assert '&lt;div class="markdown_content"&gt;' not in r


def test_sort_alpha():
    p_nbhd = M.Neighborhood.query.get(name='Projects')

    with h.push_context(p_nbhd.neighborhood_project._id):
        r = g.markdown_wiki.convert('[[projects sort=alpha]]')
        project_list = get_project_names(r)
        assert project_list == sorted(project_list)


def test_sort_registered():
    p_nbhd = M.Neighborhood.query.get(name='Projects')

    with h.push_context(p_nbhd.neighborhood_project._id):
        r = g.markdown_wiki.convert('[[projects sort=last_registered]]')
        project_names = get_project_names(r)
        ids = get_projects_property_in_the_same_order(project_names, '_id')
        assert ids == sorted(ids, reverse=True)


def test_sort_updated():
    p_nbhd = M.Neighborhood.query.get(name='Projects')

    with h.push_context(p_nbhd.neighborhood_project._id):
        r = g.markdown_wiki.convert('[[projects sort=last_updated]]')
        project_names = get_project_names(r)
        updated_at = get_projects_property_in_the_same_order(
            project_names, 'last_updated')
        assert updated_at == sorted(updated_at, reverse=True)


@with_setup(setup_functional_test)
def test_filtering():
    # set up for test
    from random import choice
    setup_trove_categories()
    random_trove = choice(M.TroveCategory.query.find().all())
    test_project = M.Project.query.get(shortname='test')
    test_project_troves = getattr(test_project, 'trove_' + random_trove.type)
    test_project_troves.append(random_trove._id)
    ThreadLocalORMSession.flush_all()

    p_nbhd = M.Neighborhood.query.get(name='Projects')
    with h.push_config(c,
                       project=p_nbhd.neighborhood_project,
                       user=M.User.by_username('test-admin')):
        r = g.markdown_wiki.convert(
            '[[projects category="%s"]]' % random_trove.fullpath)
        project_names = get_project_names(r)
        assert_equal([test_project.name], project_names)


def test_projects_macro():
    two_column_style = 'width: 330px;'

    p_nbhd = M.Neighborhood.query.get(name='Projects')
    with h.push_config(c,
                       project=p_nbhd.neighborhood_project,
                       user=M.User.anonymous()):
        # test columns
        r = g.markdown_wiki.convert('[[projects display_mode=list columns=2]]')
        assert two_column_style in r
        r = g.markdown_wiki.convert('[[projects display_mode=list columns=3]]')
        assert two_column_style not in r


@td.with_wiki
def test_limit_tools_macro():
    p_nbhd = M.Neighborhood.query.get(name='Adobe')
    with h.push_context(p_nbhd.neighborhood_project._id, 'wiki'):
        r = g.markdown_wiki.convert('[[projects]]')
        assert '<span>Admin</span>' in r
        r = g.markdown_wiki.convert('[[projects grid_view_tools=wiki]]')
        assert '<span>Admin</span>' not in r
        r = g.markdown_wiki.convert('[[projects grid_view_tools=wiki,admin]]')
        assert '<span>Admin</span>' in r


@td.with_user_project('test-admin')
@td.with_user_project('test-user-1')
def test_myprojects_macro():
    h.set_context('u/%s' % (c.user.username), 'wiki', neighborhood='Users')
    r = g.markdown_wiki.convert('[[my_projects]]')
    for p in c.user.my_projects():
        if p.deleted or p.is_nbhd_project:
            continue
        proj_title = '<h2><a href="%s">%s</a></h2>' % (p.url().rstrip('/'), p.name)
        assert_in(proj_title, r)

    h.set_context('u/test-user-1', 'wiki', neighborhood='Users')
    user = M.User.query.get(username='test-user-1')
    r = g.markdown_wiki.convert('[[my_projects]]')
    for p in user.my_projects():
        if p.deleted or p.is_nbhd_project:
            continue
        proj_title = '<h2><a href="%s">%s</a></h2>' % (p.url().rstrip('/'), p.name)
        assert_in(proj_title, r)


@td.with_wiki
def test_hideawards_macro():
    p_nbhd = M.Neighborhood.query.get(name='Projects')

    app_config_id = ObjectId()
    award = M.Award(app_config_id=app_config_id)
    award.short = 'Award short'
    award.full = 'Award full'
    award.created_by_neighborhood_id = p_nbhd._id

    project = M.Project.query.get(
        neighborhood_id=p_nbhd._id, shortname='test')

    M.AwardGrant(
        award=award,
        award_url='http://award.org',
        comment='Winner!',
        granted_by_neighborhood=p_nbhd,
        granted_to_project=project)

    ThreadLocalORMSession.flush_all()

    with h.push_context(p_nbhd.neighborhood_project._id):
        r = g.markdown_wiki.convert('[[projects]]')
        assert '<div class="feature">\n<a href="http://award.org" title="Winner!" rel="nofollow">Award short</a>\n</div>' in r, r
        r = g.markdown_wiki.convert('[[projects show_awards_banner=False]]')
        assert '<div class="feature">\n<a href="http://award.org" title="Winner!" rel="nofollow">Award short</a>\n</div>' not in r, r


def get_project_names(r):
    """
    Extracts a list of project names from a wiki page HTML.
    """
    # projects short names are in h2 elements without any attributes
    # there is one more h2 element, but it has `class` attribute
    #re_proj_names = re.compile('<h2><a[^>]>(.+)<\/a><\/h2>')
    re_proj_names = re.compile('<h2><a[^>]+>(.+)<\/a><\/h2>')
    return [e for e in re_proj_names.findall(r)]


def get_projects_property_in_the_same_order(names, prop):
    """
    Returns a list of projects properties `prop` in the same order as
    project `names`.
    It is required because results of the query are not in the same order as names.
    """
    projects = M.Project.query.find(dict(name={'$in': names})).all()
    projects_dict = dict([(p['name'], p[prop]) for p in projects])
    return [projects_dict[name] for name in names]


class TestCachedMarkdown(unittest.TestCase):

    def setUp(self):
        self.md = ForgeMarkdown()
        self.post = M.Post()
        self.post.text = '**bold**'
        self.expected_html = '<p><strong>bold</strong></p>'

    def test_bad_source_field_name(self):
        self.assertRaises(AttributeError, self.md.cached_convert,
                          self.post, 'no_such_field')

    def test_missing_cache_field(self):
        delattr(self.post, 'text_cache')
        html = self.md.cached_convert(self.post, 'text')
        self.assertEqual(html, self.expected_html)

    @patch.dict('allura.lib.app_globals.config', markdown_cache_threshold='0')
    def test_non_ascii(self):
        self.post.text = 'å∫ç'
        expected = '<p>å∫ç</p>'
        # test with empty cache
        self.assertEqual(expected, self.md.cached_convert(self.post, 'text'))
        # test with primed cache
        self.assertEqual(expected, self.md.cached_convert(self.post, 'text'))

    @patch.dict('allura.lib.app_globals.config', markdown_cache_threshold='0')
    def test_empty_cache(self):
        html = self.md.cached_convert(self.post, 'text')
        self.assertEqual(html, self.expected_html)
        self.assertEqual(html, self.post.text_cache.html)
        self.assertEqual(hashlib.md5(self.post.text).hexdigest(),
                         self.post.text_cache.md5)
        self.assertTrue(self.post.text_cache.render_time > 0)

    @patch.dict('allura.lib.app_globals.config', markdown_cache_threshold='0')
    def test_stale_cache(self):
        old = self.md.cached_convert(self.post, 'text')
        self.post.text = 'new, different source text'
        html = self.md.cached_convert(self.post, 'text')
        self.assertNotEqual(old, html)
        self.assertEqual(html, self.post.text_cache.html)
        self.assertEqual(hashlib.md5(self.post.text).hexdigest(),
                         self.post.text_cache.md5)
        self.assertTrue(self.post.text_cache.render_time > 0)

    @patch.dict('allura.lib.app_globals.config', markdown_cache_threshold='0')
    def test_valid_cache(self):
        from jinja2 import Markup
        self.md.cached_convert(self.post, 'text')
        with patch.object(self.md, 'convert') as convert_func:
            html = self.md.cached_convert(self.post, 'text')
            self.assertEqual(html, self.expected_html)
            self.assertIsInstance(html, Markup)
            self.assertFalse(convert_func.called)
            self.post.text = "text [[macro]] pass"
            html = self.md.cached_convert(self.post, 'text')
            self.assertTrue(convert_func.called)

    @patch.dict('allura.lib.app_globals.config', {})
    def test_no_threshold_defined(self):
        html = self.md.cached_convert(self.post, 'text')
        self.assertEqual(html, self.expected_html)
        self.assertIsNone(self.post.text_cache.md5)
        self.assertIsNone(self.post.text_cache.html)
        self.assertIsNone(self.post.text_cache.render_time)

    @patch.dict('allura.lib.app_globals.config', markdown_cache_threshold='foo')
    def test_invalid_threshold(self):
        html = self.md.cached_convert(self.post, 'text')
        self.assertEqual(html, self.expected_html)
        self.assertIsNone(self.post.text_cache.md5)
        self.assertIsNone(self.post.text_cache.html)
        self.assertIsNone(self.post.text_cache.render_time)

    @patch.dict('allura.lib.app_globals.config', markdown_cache_threshold='99999')
    def test_render_time_below_threshold(self):
        html = self.md.cached_convert(self.post, 'text')
        self.assertEqual(html, self.expected_html)
        self.assertIsNone(self.post.text_cache.md5)
        self.assertIsNone(self.post.text_cache.html)
        self.assertIsNone(self.post.text_cache.render_time)

    @patch.dict('allura.lib.app_globals.config', {})
    def test_all_expected_keys_exist_in_cache(self):
        self.md.cached_convert(self.post, 'text')
        required_keys = ['fix7528', 'html', 'md5', 'render_time']
        keys = sorted(self.post.text_cache.keys())
        self.assertEqual(required_keys, keys)


class TestHandlePaging(unittest.TestCase):

    def setUp(self):
        prefs = {}
        c.user = Mock()
        def get_pref(name):
            return prefs.get(name)
        def set_pref(name, value):
            prefs[name] = value
        c.user.get_pref = get_pref
        c.user.set_pref = set_pref

    def test_with_limit(self):
        self.assertEqual(g.handle_paging(10, 0), (10, 0, 0))
        self.assertEqual(g.handle_paging(10, 2), (10, 2, 20))
        # handle paging must not mess up user preferences
        self.assertEqual(c.user.get_pref('results_per_page'), None)

    def test_without_limit(self):
        # default limit = 25
        self.assertEqual(g.handle_paging(None, 0), (25, 0, 0))
        self.assertEqual(g.handle_paging(None, 2), (25, 2, 50))
        # handle paging must not mess up user preferences
        self.assertEqual(c.user.get_pref('results_per_page'), None)

        # user has page size preference
        c.user.set_pref('results_per_page', 100)
        self.assertEqual(g.handle_paging(None, 0), (100, 0, 0))
        self.assertEqual(g.handle_paging(None, 2), (100, 2, 200))
        # handle paging must not mess up user preferences
        self.assertEqual(c.user.get_pref('results_per_page'), 100)

    def test_without_limit_with_default(self):
        # default limit is not used when explicitly provided
        self.assertEqual(g.handle_paging(None, 0, 30), (30, 0, 0))
        self.assertEqual(g.handle_paging(None, 2, 30), (30, 2, 60))
        # handle paging must not mess up user preferences
        self.assertEqual(c.user.get_pref('results_per_page'), None)

        # user has page size preference, which is not affected by default
        c.user.set_pref('results_per_page', 25)
        self.assertEqual(g.handle_paging(None, 0, 30), (25, 0, 0))
        self.assertEqual(g.handle_paging(None, 2, 30), (25, 2, 50))
        # handle paging must not mess up user preferences
        self.assertEqual(c.user.get_pref('results_per_page'), 25)


class TestNeighborhoodCache(object):

    @patch('allura.lib.app_globals.M', autospec=True)
    @patch('allura.lib.app_globals.datetime', autospec=True)
    def test_lookup(self, dt_mock, M):
        dt_mock.datetime.utcnow.side_effect = [
            dt.datetime(2015, 0o2, 0o5, 11, 32),
            dt.datetime(2015, 0o2, 0o5, 11, 34),
        ]
        ret = M.Neighborhood.query.get.return_value
        cache = NeighborhoodCache(30)
        assert_equal(cache._data, {})

        n = cache._lookup('/p/')
        M.Neighborhood.query.get.assert_called_once_with(url_prefix='/p/')
        assert_equal(n, ret)
        assert_equal(cache._data, {'/p/': {
            'object': ret,
            'ts': dt.datetime(2015, 0o2, 0o5, 11, 32),
        }})

        # hits mongo every time
        n = cache._lookup('/p/')
        assert_equal(M.Neighborhood.query.get.call_count, 2)
        assert_equal(n, ret)
        assert_equal(cache._data, {'/p/': {
            'object': ret,
            'ts': dt.datetime(2015, 0o2, 0o5, 11, 34),
        }})

    @patch('allura.lib.app_globals.M', autospec=True)
    @patch('allura.lib.app_globals.datetime', autospec=True)
    def test_get(self, dt_mock, M):
        dt_mock.datetime.utcnow.side_effect = [
            dt.datetime(2015, 0o2, 0o5, 11, 32),
            dt.datetime(2015, 0o2, 0o5, 11, 34),
        ]
        ret = M.Neighborhood.query.get.return_value
        cache = NeighborhoodCache(30)
        cache._expired = Mock(return_value=False)

        n = cache.get('/p/')
        M.Neighborhood.query.get.assert_called_once_with(url_prefix='/p/')
        assert_equal(n, ret)

        # don't hit mongo second time
        n = cache.get('/p/')
        assert_equal(M.Neighborhood.query.get.call_count, 1)
        assert_equal(n, ret)

        # and hits if cache is expired
        cache._expired.return_value = True
        n = cache.get('/p/')
        assert_equal(M.Neighborhood.query.get.call_count, 2)
        assert_equal(n, ret)

    @patch('allura.lib.app_globals.datetime', autospec=True)
    def test_expired(self, dt_mock):
        dt_mock.timedelta = dt.timedelta  # restore original
        _now = dt.datetime(2015, 0o2, 0o5, 11, 53)
        dt_mock.datetime.utcnow.return_value = _now

        cache = NeighborhoodCache(0)
        assert_equal(cache._expired({'ts': _now}), True)
        assert_equal(cache._expired({'ts': _now - dt.timedelta(seconds=1)}), True)

        cache = NeighborhoodCache(30)
        assert_equal(cache._expired({'ts': _now - dt.timedelta(seconds=29)}), False)
        assert_equal(cache._expired({'ts': _now - dt.timedelta(seconds=30)}), True)