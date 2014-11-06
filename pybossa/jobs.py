# -*- coding: utf8 -*-
# This file is part of PyBossa.
#
# Copyright (C) 2014 SF Isle of Man Limited
#
# PyBossa is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PyBossa is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with PyBossa.  If not, see <http://www.gnu.org/licenses/>.
"""Jobs module for running background tasks in PyBossa server."""
import os

from pybossa.core import mail

def get_scheduled_jobs():
    return [warm_up_stats, warn_old_project_owners]

def warm_up_stats():
    """Background job for warming stats."""
    print "Running on the background warm_up_stats"
    from pybossa.cache.site_stats import (n_auth_users, n_anon_users,
                                          n_tasks_site, n_total_tasks_site,
                                          n_task_runs_site,
                                          get_top5_apps_24_hours,
                                          get_top5_users_24_hours, get_locs)

    env_cache_disabled = os.environ.get('PYBOSSA_REDIS_CACHE_DISABLED')
    if not env_cache_disabled:
        os.environ['PYBOSSA_REDIS_CACHE_DISABLED'] = '1'

    n_auth_users()
    n_anon_users()
    n_tasks_site()
    n_total_tasks_site()
    n_task_runs_site()
    get_top5_apps_24_hours()
    get_top5_users_24_hours()
    get_locs()

    if env_cache_disabled is None:
        del os.environ['PYBOSSA_REDIS_CACHE_DISABLED']
    else:
        os.environ['PYBOSSA_REDIS_CACHE_DISABLED'] = env_cache_disabled

    return True


def send_mail(message_dict):
    from flask.ext.mail import Message
    message = Message(**message_dict)
    mail.send(message)


def warm_cache():
    """Background job to warm cache."""
    from pybossa.core import create_app
    app = create_app(run_as_server=False)
    # Disable cache, so we can refresh the data in Redis
    env_cache_disabled = os.environ.get('PYBOSSA_REDIS_CACHE_DISABLED')
    if not env_cache_disabled:
        os.environ['PYBOSSA_REDIS_CACHE_DISABLED'] = '1'
    # Cache 3 pages
    apps_cached = []
    pages = range(1, 4)
    import pybossa.cache.apps as cached_apps
    import pybossa.cache.categories as cached_cat
    import pybossa.cache.users as cached_users
    import pybossa.cache.project_stats as stats

    def warm_app(id, short_name, featured=False):
        if id not in apps_cached:
            cached_apps.get_app(short_name)
            cached_apps.n_tasks(id)
            n_task_runs = cached_apps.n_task_runs(id)
            cached_apps.overall_progress(id)
            cached_apps.last_activity(id)
            cached_apps.n_completed_tasks(id)
            cached_apps.n_volunteers(id)
            if n_task_runs >= 1000 or featured:
                print ("Getting stats for %s as it has %s task runs" %
                       (short_name, n_task_runs))
                stats.get_stats(id, app.config.get('GEO'))
            apps_cached.append(id)

    # Cache top projects
    apps = cached_apps.get_top()
    for a in apps:
        warm_app(a['id'], a['short_name'])
    for page in pages:
        apps = cached_apps.get_featured('featured', page,
                                        app.config['APPS_PER_PAGE'])
        for a in apps:
            warm_app(a['id'], a['short_name'], featured=True)

    # Categories
    categories = cached_cat.get_used()
    for c in categories:
        for page in pages:
            apps = cached_apps.get(c['short_name'],
                                   page,
                                   app.config['APPS_PER_PAGE'])
            for a in apps:
                warm_app(a['id'], a['short_name'])
    # Users
    cached_users.get_leaderboard(app.config['LEADERBOARD'], 'anonymous')
    cached_users.get_top()

    if env_cache_disabled is None:
        del os.environ['PYBOSSA_REDIS_CACHE_DISABLED']
    else:
        os.environ['PYBOSSA_REDIS_CACHE_DISABLED'] = env_cache_disabled

    return True


def get_non_updated_apps():
    """Return a list of non updated apps."""
    from sqlalchemy.sql import text
    from pybossa.model.app import App
    from pybossa.core import db
    sql = text('''SELECT id FROM app WHERE TO_DATE(updated,
                'YYYY-MM-DD\THH24:MI:SS.US') <= NOW() - '3 month':: INTERVAL
               AND contacted != True LIMIT 25''')
    results = db.slave_session.execute(sql)
    apps = []
    for row in results:
        a = App.query.get(row.id)
        apps.append(a)
    return apps


def warn_old_project_owners():
    """E-mail the project owners not updated in the last 3 months."""
    from pybossa.core import mail, db
    from flask import current_app
    from flask.ext.mail import Message

    apps = get_non_updated_apps()

    with mail.connect() as conn:
        for a in apps:
            message = ("Dear %s,\
                       \
                       Your project %s has been inactive for the last 3 months.\
                       And we would like to inform you that if you need help \
                       with it, just contact us answering to this email.\
                       \
                       Otherwise, we will archive the project, removing it \
                       from the server. You have one month to upload any new \
                       tasks, add a new blog post, or engage new volunteers.\
                       \
                       If at the end the project is deleted, we will send you \
                       a ZIP file where you can download your project.\
                       \
                       All the best,\
                       \
                       The team.") % (a.owner.fullname, a.name)
            subject = ('Your %s project: %s has been inactive'
                       % (current_app.config.get('BRAND'), a.name))
            msg = Message(recipients=[a.owner.email_addr],
                          body=message,
                          subject=subject)
            conn.send(msg)
            a.contacted = True
            db.session.add(a)
            db.session.commit()
    return True
