from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from __future__ import unicode_literals
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

import os
import logging
import difflib
from urllib.parse import quote, unquote
from collections import defaultdict, OrderedDict
from itertools import islice

from paste.deploy.converters import asbool
from pylons import tmpl_context as c, app_globals as g
from pylons import request, response
from webob import exc
import tg
from tg import redirect, expose, flash, validate
from tg.decorators import with_trailing_slash, without_trailing_slash
from tg import session as web_session
from formencode import validators
from bson import ObjectId
from ming.base import Object
from ming.orm import ThreadLocalORMSession, session

import allura.tasks
from allura import model as M
from allura.lib import utils
from allura.lib import helpers as h
from allura.lib import widgets as w
from allura.lib.decorators import require_post
from allura.lib.diff import HtmlSideBySideDiff
from allura.lib.security import require_access, require_authenticated
from allura.lib.widgets import form_fields as ffw
from allura.lib.widgets.repo import SCMLogWidget, SCMRevisionWidget, SCMTreeWidget
from allura.lib.widgets.repo import SCMMergeRequestWidget, SCMMergeRequestFilterWidget
from allura.lib.widgets.repo import SCMMergeRequestDisposeWidget, SCMCommitBrowserWidget
from allura.lib.widgets.subscriptions import SubscribeForm
from allura.controllers import AppDiscussionController
from allura.controllers.base import DispatchIndex
from allura.controllers.feed import FeedController, FeedArgs
from .base import BaseController

log = logging.getLogger(__name__)


def on_import():
    BranchBrowser.CommitBrowserClass = CommitBrowser
    CommitBrowser.TreeBrowserClass = TreeBrowser
    TreeBrowser.FileBrowserClass = FileBrowser


class RepoRootController(BaseController, FeedController):
    _discuss = AppDiscussionController()
    commit_browser_widget = SCMCommitBrowserWidget()

    def get_feed(self, project, app, user):
        query = dict(project_id=project._id, app_config_id=app.config._id)
        pname, repo = (project.shortname, app.config.options.mount_label)
        title = '%s %s changes' % (pname, repo)
        description = 'Recent changes to %s repository in %s project' % (
            repo, pname)
        return FeedArgs(query, title, app.url, description=description)

    def _check_security(self):
        require_access(c.app, 'read')

    @expose('jinja:allura:templates/markdown_syntax_dialog.html')
    def markdown_syntax_dialog(self):
        """Static page explaining markdown."""
        return dict()

    @with_trailing_slash
    @expose()
    def index(self, offset=0, branch=None, **kw):
        if branch is None:
            branch = c.app.default_branch_name
        redirect(c.app.repo.url_for_commit(branch, url_type='ref'))

    @with_trailing_slash
    @expose('jinja:allura:templates/repo/forks.html')
    def forks(self, **kw):

        links = []
        if c.app.repo.forks:
            for f in c.app.repo.forks:
                repo_path_parts = f.url().strip('/').split('/')
                links.append(dict(
                    repo_url=f.url(),
                    repo='%s / %s' % (repo_path_parts[1],
                                      repo_path_parts[-1]),
                ))
        return dict(links=links)

    @expose()
    def refresh(self, **kw):
        allura.tasks.repo_tasks.refresh.post()
        if request.referer:
            flash('Repository is being refreshed')
            redirect(request.referer)
        else:
            return '%r refresh queued.\n' % c.app.repo

    @with_trailing_slash
    @expose('jinja:allura:templates/repo/fork.html')
    def fork(self, project_id=None, mount_point=None, mount_label=None, **kw):
        # this shows the form and handles the submission
        require_authenticated()
        if not c.app.forkable:
            raise exc.HTTPNotFound
        from_repo = c.app.repo
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        from_project = c.project
        to_project = M.Project.query.get(_id=ObjectId(project_id))
        mount_label = mount_label or '%s - %s' % (c.project.name,
                                                  c.app.config.options.mount_label)
        mount_point = (mount_point or from_project.shortname)
        if request.method != 'POST' or not mount_point:
            return dict(from_repo=from_repo,
                        user_project=c.user.private_project(),
                        mount_point=mount_point,
                        mount_label=mount_label)
        else:
            with h.push_config(c, project=to_project):
                if not to_project.database_configured:
                    to_project.configure_project(is_user_project=True)
                require_access(to_project, 'admin')
                try:
                    to_project.install_app(
                        ep_name=from_repo.tool_name,
                        mount_point=mount_point,
                        mount_label=mount_label,
                        cloned_from_project_id=from_project._id,
                        cloned_from_repo_id=from_repo._id)
                    redirect(to_project.url() + mount_point + '/')
                except exc.HTTPRedirection:
                    raise
                except Exception as ex:
                    flash(str(ex), 'error')
                    redirect(request.referer)

    @property
    def mr_widget(self):
        source_branches = [
            b.name
            for b in c.app.repo.get_branches() + c.app.repo.get_tags()]
        with c.app.repo.push_upstream_context():
            target_branches = [
                b.name
                for b in c.app.repo.get_branches() + c.app.repo.get_tags()]
        return SCMMergeRequestWidget(
            source_branches=source_branches,
            target_branches=target_branches)

    @without_trailing_slash
    @expose('jinja:allura:templates/repo/request_merge.html')
    def request_merge(self, branch=None, **kw):
        require_access(c.app.repo, 'admin')
        c.form = self.mr_widget
        if branch in c.form.source_branches:
            source_branch = branch
        else:
            source_branch = c.app.default_branch_name
        with c.app.repo.push_upstream_context():
            target_branch = c.app.default_branch_name
        return {
            'source_branch': source_branch,
            'target_branch': target_branch,
        }

    @expose()
    @require_post()
    def do_request_merge(self, **kw):
        kw = self.mr_widget.to_python(kw)
        downstream = dict(
            project_id=c.project._id,
            mount_point=c.app.config.options.mount_point,
            commit_id=c.app.repo.commit(kw['source_branch'])._id)
        with c.app.repo.push_upstream_context():
            mr = M.MergeRequest.upsert(
                downstream=downstream,
                target_branch=kw['target_branch'],
                source_branch=kw['source_branch'],
                summary=kw['summary'],
                description=kw['description'])
            M.Notification.post(
                mr, 'merge_request',
                subject='Merge request: ' + mr.summary)
            t = M.Thread.new(
                discussion_id=c.app.config.discussion_id,
                artifact_reference=mr.index_id(),
                subject='Discussion for Merge Request #:%s: %s' % (
                    mr.request_number, mr.summary))
            session(t).flush()
            redirect(mr.url())

    @without_trailing_slash
    @expose('jinja:allura:templates/repo/commit_browser.html')
    def commit_browser(self, **kw):
        if not c.app.repo or c.app.repo.status != 'ready':
            return dict(status='not_ready')
        # if c.app.repo.count() > 2000:
        #     return dict(status='too_many_commits')
        if c.app.repo.is_empty():
            return dict(status='no_commits')
        c.commit_browser_widget = self.commit_browser_widget
        return dict(status='ready')

    @without_trailing_slash
    @expose('json:')
    def commit_browser_data(self, **kw):
        head_ids = [head.object_id for head in c.app.repo.get_heads()]
        commit_ids = [c.app.repo.rev_to_commit_id(r)
                      for r in c.app.repo.log(head_ids, id_only=True)]
        log.info('Grab %d commit objects by ID', len(commit_ids))
        commits_by_id = {
            c_obj._id: c_obj
            for c_obj in M.repository.CommitDoc.m.find(dict(_id={'$in': commit_ids}))}
        log.info('... build graph')
        parents = {}
        children = defaultdict(list)
        dates = {}
        for row, (oid, ci) in enumerate(commits_by_id.items()):
            parents[oid] = list(ci.parent_ids)
            dates[oid] = ci.committed.date
            for p_oid in ci.parent_ids:
                children[p_oid].append(oid)
        result = []
        for row, oid in enumerate(topo_sort(children, parents, dates, head_ids)):
            ci = commits_by_id[oid]
            url = c.app.repo.url_for_commit(Object(_id=oid))
            msg_split = ci.message.splitlines()
            if msg_split:
                msg = msg_split[0]
            else:
                msg = "No commit message."
            result.append(dict(
                oid=oid,
                short_id=c.app.repo.shorthand_for_commit(oid),
                row=row,
                parents=ci.parent_ids,
                message=msg,
                url=url))
        log.info('...done')
        col_idx = {}
        columns = []

        def find_column(columns):
            for i, col in enumerate(columns):
                if col is None:
                    return i
            columns.append(None)
            return len(columns) - 1
        for row, ci_json in enumerate(result):
            oid = ci_json['oid']
            colno = col_idx.get(oid)
            if colno is None:
                colno = find_column(columns)
                col_idx[oid] = colno
            columns[colno] = None
            ci_json['column'] = colno
            for p in parents[oid]:
                p_col = col_idx.get(p, None)
                if p_col is not None:
                    continue
                p_col = find_column(columns)
                col_idx[p] = p_col
                columns[p_col] = p
        built_tree = dict(
            (ci_json['oid'], ci_json) for ci_json in result)
        return dict(
            commits=[ci_json['oid'] for ci_json in result],
            built_tree=built_tree,
            next_column=len(columns),
            max_row=row)

    @expose('json:')
    def status(self, **kw):
        return dict(status=c.app.repo.status)


class RepoRestController(RepoRootController):

    @expose('json:')
    def index(self, **kw):
        all_commits = c.app.repo._impl.new_commits(all_commits=True)
        return dict(commit_count=len(all_commits))

    @expose('json:')
    def commits(self, rev=None, **kw):
        revisions = islice(c.app.repo.log(rev, id_only=False), 25)

        return {
            'commits': [
                {
                    'parents': [{'id': p} for p in commit['parents']],
                    'url': c.app.repo.url_for_commit(commit['id']),
                    'id': commit['id'],
                    'message': commit['message'],
                    'tree': commit.get('tree'),
                    'committed_date': commit['committed']['date'],
                    'authored_date': commit['authored']['date'],
                    'author': {
                        'name': commit['authored']['name'],
                        'email': commit['authored']['email'],
                    },
                    'committer': {
                        'name': commit['committed']['name'],
                        'email': commit['committed']['email'],
                    },
                }
                for commit in revisions
            ]}


class MergeRequestsController(object):
    mr_filter = SCMMergeRequestFilterWidget()

    @with_trailing_slash
    @expose('jinja:allura:templates/repo/merge_requests.html')
    @validate(mr_filter)
    def index(self, status=None, **kw):
        status = status or ['open']
        requests = c.app.repo.merge_requests_by_statuses(*status)
        c.mr_filter = self.mr_filter
        return dict(
            status=status,
            requests=requests)

    @expose()
    def _lookup(self, num, *remainder):
        return MergeRequestController(num), remainder


class MergeRequestController(object):
    log_widget = SCMLogWidget(show_paging=False)
    thread_widget = w.Thread(
        page=None, limit=None, page_size=None, count=None,
        style='linear')
    mr_dispose_form = SCMMergeRequestDisposeWidget()

    def __init__(self, num):
        self.tmpl = g.jinja2_env.get_template('allura:templates/repo/merge_request_changed.html')
        self.req = M.MergeRequest.query.get(
            app_config_id=c.app.config._id,
            request_number=int(num))
        if self.req is None:
            raise exc.HTTPNotFound

    @with_trailing_slash
    @expose('jinja:allura:templates/repo/merge_request.html')
    def index(self, page=0, limit=250, **kw):
        c.thread = self.thread_widget
        c.log_widget = self.log_widget
        c.mr_dispose_form = self.mr_dispose_form
        with self.req.push_downstream_context():
            downstream_app = c.app
        result = dict(
            downstream_app=downstream_app,
            req=self.req,
            can_merge=self.req.can_merge(),
            can_merge_status=self.req.can_merge_task_status(),
            merge_status=self.req.merge_task_status(),
            page=page,
            limit=limit,
            count=self.req.discussion_thread.post_count)
        try:
            result['commits'] = self.req.commits
        except Exception:
            log.info(
                "Can't get commits for merge request %s",
                self.req.url(),
                exc_info=True)
            result['commits'] = []
            result['error'] = True
        return result

    @property
    def mr_widget_edit(self):
        target_branches = [
            b.name
            for b in c.app.repo.get_branches() + c.app.repo.get_tags()]
        with self.req.push_downstream_context():
            source_branches = [
                b.name
                for b in c.app.repo.get_branches() + c.app.repo.get_tags()]
        return SCMMergeRequestWidget(
            source_branches=source_branches,
            target_branches=target_branches)

    @expose('jinja:allura:templates/repo/merge_request_edit.html')
    def edit(self, **kw):
        require_access(self.req, 'write')
        c.form = self.mr_widget_edit
        if self.req['source_branch'] in c.form.source_branches:
            source_branch = self.req['source_branch']
        else:
            source_branch = c.app.default_branch_name
        if self.req['target_branch'] in c.form.target_branches:
            target_branch = self.req['target_branch']
        else:
            target_branch = c.app.default_branch_name
        return {
            'source_branch': source_branch,
            'target_branch': target_branch,
            'description': self.req['description'],
            'summary': self.req['summary']
        }

    @expose()
    @require_post()
    def do_request_merge_edit(self, **kw):
        require_access(self.req, 'write')
        kw = self.mr_widget_edit.to_python(kw)
        changes = OrderedDict()
        if self.req.summary != kw['summary']:
            changes['Summary'] = [self.req.summary, kw['summary']]
            self.req.summary = kw['summary']

        if self.req.target_branch != kw['target_branch']:
            changes['Target branch'] = [self.req.target_branch, kw['target_branch']]
            self.req.target_branch = kw['target_branch']

        if self.req.source_branch != kw['source_branch']:
            changes['Source branch'] = [self.req.source_branch, kw['source_branch']]
            self.req.source_branch = kw['source_branch']

        if self.req.description != kw['description']:
            changes['Description'] = h.unidiff(self.req.description, kw['description'])
            self.req.description = kw['description']
        with self.req.push_downstream_context():
            self.req.downstream['commit_id'] = c.app.repo.commit(kw['source_branch'])._id

        message = self.tmpl.render(changes=changes)
        self.req.discussion_thread.add_post(text=message, is_meta=True)
        redirect(self.req.url())

    @expose()
    @require_post()
    @validate(mr_dispose_form)
    def save(self, status=None, **kw):
        require_access(self.req, 'write')
        if self.req.status != status:
            message = self.tmpl.render(changes={'Status': [self.req.status, status]})
            self.req.discussion_thread.add_post(text=message, is_meta=True)
            self.req.status = status
        redirect('.')

    @expose()
    @require_post()
    def merge(self):
        if not self.req.merge_allowed(c.user) or not self.req.can_merge():
            raise exc.HTTPNotFound
        self.req.merge()
        redirect(self.req.url())

    @expose('json:')
    def merge_task_status(self):
        return {'status': self.req.merge_task_status()}

    @expose('json:')
    def can_merge_task_status(self):
        return {'status': self.req.can_merge_task_status()}

    @expose('json:')
    def can_merge_result(self):
        """Return result from the cache. Used by js, after task was completed."""
        return {'can_merge': self.req.can_merge()}


class RefsController(object):

    def __init__(self, BranchBrowserClass):
        self.BranchBrowserClass = BranchBrowserClass

    @expose()
    def _lookup(self, ref, *remainder):
        EOR = quote(c.app.END_OF_REF_ESCAPE)
        if EOR in remainder:
            i = remainder.index(quote(c.app.END_OF_REF_ESCAPE))
            ref = '/'.join((ref,) + remainder[:i])
            remainder = remainder[i + 1:]
        return self.BranchBrowserClass(ref), remainder


class CommitsController(object):

    @expose()
    def _lookup(self, ci, *remainder):
        EOR = quote(c.app.END_OF_REF_ESCAPE)
        if EOR in remainder:
            i = remainder.index(quote(c.app.END_OF_REF_ESCAPE))
            ci = '/'.join((ci,) + remainder[:i])
            remainder = remainder[i + 1:]
        return CommitBrowser(ci), remainder


class BranchBrowser(BaseController):
    CommitBrowserClass = None

    def __init__(self, branch):
        self._branch = branch

    def _check_security(self):
        require_access(c.app.repo, 'read')

    @expose('jinja:allura:templates/repo/tags.html')
    @with_trailing_slash
    def tags(self, **kw):
        return dict(tags=c.app.repo.get_tags())

    @expose('jinja:allura:templates/repo/tags.html')
    @with_trailing_slash
    def branches(self, **kw):
        return dict(title='Branches', tags=c.app.repo.get_branches())

    @expose()
    @with_trailing_slash
    def log(self, **kw):
        ci = c.app.repo.commit(self._branch)
        redirect(ci.url() + 'log/')


class CommitBrowser(BaseController):
    TreeBrowserClass = None
    revision_widget = SCMRevisionWidget()
    log_widget = SCMLogWidget()
    page_list = ffw.PageList()
    DEFAULT_PAGE_LIMIT = 25

    def __init__(self, revision):
        self._revision = revision
        self._commit = c.app.repo.commit(revision)
        c.revision = revision
        if self._commit is None:
            raise exc.HTTPNotFound
        self.tree = self.TreeBrowserClass(self._commit, tree=self._commit.tree)

    @expose('jinja:allura:templates/repo/commit.html')
    @validate(dict(page=validators.Int(if_empty=0, if_invalid=0),
                   limit=validators.Int(if_empty=DEFAULT_PAGE_LIMIT, if_invalid=DEFAULT_PAGE_LIMIT)))
    def index(self, page=0, limit=DEFAULT_PAGE_LIMIT, **kw):
        c.revision_widget = self.revision_widget
        c.page_list = self.page_list
        result = dict(commit=self._commit)
        if self._commit:
            result.update(self._commit.context())
        tree = self._commit.tree
        limit, page, start = g.handle_paging(limit, page,
                                             default=self.DEFAULT_PAGE_LIMIT)
        diffs = self._commit.paged_diffs(start=start, end=start + limit)
        result['artifacts'] = [
            (t, f, 'blob' if tree.get_blob_by_path(f) else 'tree')
            for t in ('added', 'removed', 'changed', 'copied')
            for f in diffs[t]]
        count = diffs['total']
        result.update(dict(page=page, limit=limit, count=count))
        return result

    @expose('jinja:allura:templates/repo/commit_basic.html')
    def basic(self, **kw):
        c.revision_widget = self.revision_widget
        result = dict(commit=self._commit)
        if self._commit:
            result.update(self._commit.context())
        return result

    @expose('jinja:allura:templates/repo/tarball.html')
    def tarball(self, **kw):
        path = request.params.get('path')
        if not asbool(tg.config.get('scm.repos.tarball.enable', False)):
            raise exc.HTTPNotFound()
        rev = self._commit.url().split('/')[-2]
        status = c.app.repo.get_tarball_status(rev, path)
        if not status and request.method == 'POST':
            allura.tasks.repo_tasks.tarball.post(rev, path)
            redirect('tarball' + '?path={0}'.format(path) if path else '')
        return dict(commit=self._commit, revision=rev, status=status)

    @expose('json:')
    def tarball_status(self, path=None, **kw):
        if not asbool(tg.config.get('scm.repos.tarball.enable', False)):
            raise exc.HTTPNotFound()
        rev = self._commit.url().split('/')[-2]
        return dict(status=c.app.repo.get_tarball_status(rev, path))

    @expose('jinja:allura:templates/repo/log.html')
    @with_trailing_slash
    @validate(dict(page=validators.Int(if_empty=0, if_invalid=0),
                   limit=validators.Int(if_empty=25, if_invalid=25)))
    def log(self, limit=25, path=None, **kw):
        is_file = False
        if path:
            is_file = c.app.repo.is_file(path, self._commit._id)
        commits = list(islice(c.app.repo.log(
            revs=self._commit._id,
            path=path,
            id_only=False,
            page_size=limit + 1), limit + 1))
        next_commit = None
        if len(commits) > limit:
            next_commit = commits.pop()
        c.log_widget = self.log_widget
        return dict(
            username=c.user._id and c.user.username,
            branch=None,
            log=commits,
            next_commit=next_commit,
            limit=limit,
            path=path,
            is_file=is_file,
            **kw)


class TreeBrowser(BaseController, DispatchIndex):
    tree_widget = SCMTreeWidget()
    FileBrowserClass = None
    subscribe_form = SubscribeForm()

    def __init__(self, commit, tree, path='', parent=None):
        self._commit = commit
        self._tree = tree
        self._path = path
        self._parent = parent

    @expose('jinja:allura:templates/repo/tree.html')
    @with_trailing_slash
    def index(self, **kw):
        c.tree_widget = self.tree_widget
        c.subscribe_form = self.subscribe_form
        tool_subscribed = M.Mailbox.subscribed()
        tarball_url = None
        if asbool(tg.config.get('scm.repos.tarball.enable', False)):
            cutout = len('tree' + self._path)
            if request.path.endswith('/') and not self._path.endswith('/'):
                cutout += 1
            tarball_url = quote('%starball' % unquote(request.path)[:-cutout])
        return dict(
            repo=c.app.repo,
            commit=self._commit,
            tree=self._tree,
            path=self._path,
            parent=self._parent,
            tool_subscribed=tool_subscribed,
            tarball_url=tarball_url)

    @expose()
    def _lookup(self, next, *rest):
        next = h.really_unicode(unquote(next))
        if not rest:
            # Might be a file rather than a dir
            filename = h.really_unicode(
                unquote(
                    request.environ['PATH_INFO'].rsplit('/')[-1]))
            if filename:
                try:
                    obj = self._tree[filename]
                except KeyError:
                    raise exc.HTTPNotFound()
                if isinstance(obj, M.repository.Blob):
                    return self.FileBrowserClass(
                        self._commit,
                        self._tree,
                        filename), rest
        elif rest == ('index', ):
            rest = (request.environ['PATH_INFO'].rsplit('/')[-1],)
        try:
            tree = self._tree[next]
        except KeyError:
            raise exc.HTTPNotFound
        return self.__class__(
            self._commit,
            tree,
            self._path + '/' + next,
            self), rest

    @expose()
    @validate(subscribe_form)
    def subscribe(self, subscribe=None, unsubscribe=None, **kw):
        if subscribe:
            M.Mailbox.subscribe()
        elif unsubscribe:
            M.Mailbox.unsubscribe()
        redirect(request.referer)


class FileBrowser(BaseController):

    def __init__(self, commit, tree, filename):
        self._commit = commit
        self._tree = tree
        self._filename = filename
        self._blob = self._tree.get_blob(filename)

    @expose('jinja:allura:templates/repo/file.html')
    def index(self, **kw):
        if kw.pop('format', 'html') == 'raw':
            return self.raw()
        elif 'diff' in kw:
            tg.decorators.override_template(
                self.index, 'jinja:allura:templates/repo/diff.html')
            return self.diff(kw['diff'], kw.pop('diformat', None))
        elif 'barediff' in kw:
            tg.decorators.override_template(
                self.index, 'jinja:allura:templates/repo/barediff.html')
            return self.diff(kw['barediff'], kw.pop('diformat', None))
        else:
            force_display = 'force' in kw
            stats = utils.generate_code_stats(self._blob)
            return dict(
                blob=self._blob,
                stats=stats,
                force_display=force_display
            )

    @expose()
    def raw(self, **kw):
        content_type = self._blob.content_type.encode('utf-8')
        filename = self._blob.name.encode('utf-8')
        response.headers['Content-Type'] = ''
        response.content_type = content_type
        if self._blob.content_encoding is not None:
            content_encoding = self._blob.content_encoding.encode('utf-8')
            response.headers['Content-Encoding'] = ''
            response.content_encoding = content_encoding
        response.headers.add(
            'Content-Disposition', 'attachment;filename=' + filename)
        return iter(self._blob)

    def diff(self, commit, fmt=None, **kw):
        try:
            path, filename = os.path.split(self._blob.path())
            a_ci = c.app.repo.commit(commit)
            a = a_ci.get_path(self._blob.path())
            apath = a.path()
        except:
            a = []
            apath = ''
        b = self._blob

        if not self._blob.has_html_view:
            diff = "Cannot display: file marked as a binary type."
            return dict(a=a, b=b, diff=diff)

        la = list(a)
        lb = list(b)
        adesc = ('a' + h.really_unicode(apath)).encode('utf-8')
        bdesc = ('b' + h.really_unicode(b.path())).encode('utf-8')

        if not fmt:
            fmt = web_session.get('diformat', '')
        else:
            web_session['diformat'] = fmt
            web_session.save()
        if fmt == 'sidebyside':
            hd = HtmlSideBySideDiff()
            diff = hd.make_table(la, lb, adesc, bdesc)
        else:
            diff = ''.join(difflib.unified_diff(la, lb, adesc, bdesc))
        return dict(a=a, b=b, diff=diff)


def topo_sort(children, parents, dates, head_ids):
    to_visit = sorted(list(set(head_ids)), key=lambda x: dates[x])
    visited = set()
    while to_visit:
        next = to_visit.pop()
        if next in visited:
            continue
        visited.add(next)
        yield next
        for p in parents[next]:
            for child in children[p]:
                if child not in visited:
                    break
            else:
                to_visit.append(p)

on_import()