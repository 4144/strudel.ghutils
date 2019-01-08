#!/usr/bin/env python
# -*- coding: utf8 -*-

"""
This module provides interfaces to "unofficial GitHub API".
Some

- get user contributions timeline
- user contribution stats
    (crude but fast version of the contributions timeline)
- get project weekly contributors stats

"""

from __future__ import print_function

import argparse
from collections import defaultdict
import datetime
from functools import wraps
import logging
import re
import six  # Queue
import threading
import time
from typing import *
import warnings
from xml.etree import ElementTree

from bs4 import BeautifulSoup
import feedparser
import pandas as pd
import requests

__version__ = "0.0.1"
__author__ = "Marat (@cmu.edu)"
__license__ = "GPL v3"

BASE_URL = 'https://github.com'
HEADERS = {   # browser headers for non-API URLs
    'X-Requested-With': 'XMLHttpRequest',
    'Accept-Encoding': "gzip,deflate,br",
    'Accept': "*/*",
    'Origin': BASE_URL,
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:60.0) "
                  "Gecko/20100101 Firefox/60.0",
    "Host": 'github.com',
    "Referer": BASE_URL,
    "DNT": "1",
    "Accept-Language": 'en-US,en;q=0.5',
    "Connection": "keep-alive",
    "Cache-Control": 'max-age=0',
}


def normalize_text(s):
    # type: (str) -> str
    """ Normalize spaces and newlines
    >>> normalize_text("\\nHello   world  \\t\\n!")
    'Hello world!'
    """
    return " ".join(s.split())


def extract_repo(link):
    # type: (str) -> str
    """ Extract repository slug from a GitHub link

    >>> extract_repo("/org/repo/blabla?something=foo")
    'org/repo'
    >>> extract_repo("org/repo")
    'org/repo'
    """
    return "/".join(link.strip("/").split("/", 2)[:2])


def _parse_timeline_update_record(record_div):
    # type(BeautifulSoup) -> dict
    """
    Args:
        record_div(BeautifulSoup): a BS4 HTML element object,
            representing one chunk of GitHub user activity.
    Returns:
        Dict[str, Dict[str, int]]: {
            repository1: {
                'commits': ...,
                'issues': ...,
                'pull_requests': ...,
                'reviews': ...,
                'created_repository': {0|1},
            }
        }
    """
    # Note: GitHub lists only first 25 repos for each activity
    # data[repo][activity] = <number>
    record_data = defaultdict(lambda: defaultdict(int))

    # get record title:
    if record_div.button:
        # created commits, repositories, issues,
        # reviewed pull requests
        title = normalize_text(record_div.button.text)
        if re.match(
                'Reviewed \\d+ pull requests? in \\d+ repositor(y|ies)', title):

            for repo_div in record_div.find_all(
                    'div', class_='profile-rollup-summarized'):
                repo_span, count_span = repo_div.button.find_all('span')
                repo = repo_span.text
                count = int(count_span.text.split()[0])
                record_data[repo]['reviews'] += count

        elif re.match('Opened \\d+ (?:other )?issues? in \\d+ repositor(y|ies)',
                      title):
            for repo_div in record_div.find_all(
                    'div', class_='profile-rollup-summarized'):
                repo = repo_div.button.div.span.text
                count = 0
                count_span = repo_div.button.find_all(
                    'span', recursive=False)[0]
                for span in count_span.find_all('span'):
                    count += int(span.text)
                record_data[repo]['issues'] += count

        elif re.match('Created \\d+ repositor(y|ies)', title):
            for link in record_div.find_all(
                    'a', attrs={'data-hovercard-type': "repository"}):
                record_data[link.text]['created_repository'] = 1

        elif re.match('Opened \\d+ (?:other )?pull requests? '
                      'in \\d+ repositor(y|ies)', title):
            for repo_div in record_div.find_all(
                    'div', class_='profile-rollup-summarized'):
                repo = repo_div.button.div.span.text
                count = 0
                count_span = repo_div.button.find_all('span', recursive=False)[
                    0]
                for span in count_span.find_all('span'):
                    count += int(span.text)
                record_data[repo]['pull_requests'] += count

        elif re.match('Created \\d+ commits? in \\d+ repositor(y|ies)', title):
            for repo_li in record_div.ul.find_all('li', recursive=False):
                li_div = repo_li.div
                if not li_div:
                    continue  # "N repositories not shown"
                repo_link = li_div.find_all('a', recursive=False)[1]
                repo = extract_repo(repo_link["href"])
                count = int(repo_link.text.strip().split(" ")[0])
                record_data[repo]['commits'] += count

        else:
            raise ValueError("Unexpected title: %s\n%s"
                             "" % (title, str(record_div)))

    elif record_div.h4:
        title = normalize_text(record_div.h4.text)
        repo = record_div.h4.a and record_div.h4.a.text
        if title.startswith("Created an issue in"):
            record_data[repo]['issues'] += 1
        elif title.startswith("Joined the"):
            record_data[record_div.a['href'].strip('/')]['joined_org'] = 1
        elif title.startswith("Created a pull request in"):
            # fist PR in a given month
            record_data[repo]['pull_requests'] += 1
        elif title == "Joined GitHub":
            pass
        elif title.startswith("Opened their first issue on GitHub in"):
            record_data[repo]['issues'] += 1
        elif title.startswith("Opened their first pull request on GitHub in"):
            record_data[repo]['pull_requests'] += 1
        elif title.startswith("Created their first repository"):
            link = record_div.find_all(
                'a', attrs={'data-hovercard-type': "repository"})[0]
            repo = extract_repo(link.get('href'))
            record_data[repo]['created_repository'] = 1
        else:
            raise ValueError("Unexpected title: " + title)

    elif len(record_div.span) == 3:
        # private activity
        title = normalize_text(record_div.find_all('span')[1].text)
        if title.endswith(' in private repositories'):
            record_data[None]['private_contrib'] += int(title.split(" ", 1)[0])
        else:
            raise ValueError("Unexpected title: " + title)
    else:
        raise ValueError("Unexpected activity:" + str(record_div))

    # convert defaultdict to dict
    return {rep: dict(activities) for rep, activities in record_data.items()}


def _parse_timeline_update(bs4_tree):
    # type(BeautifulSoup) -> tuple
    """ Parse a chunk of activity acquired via Ajax, usually one month.

    Returns:
        Generator[Tuple[str, Dict[str, int]]]:
            (month, {output of parse_record()})

    <div class="contribution-activity-listing">  # month div
        <div class="profile-timeline discussion-timeline">  # one extra wrapper
            <h3>  # month title
            <div class="profile-rollup-wrapper">  # record divs
            ...

    Terminology:
        timeline consists of updates
        updates contain one or more months. Only one month is non-empty
        month cosists of records - a single chunk of reported activity
        record might contain information about several repositories,
            e.g. Created N commits in M repositories
    """
    # sometimes next chunk includes several months.
    # In these cases, all except one are empty;
    # often empty "months" represent ranges, e.g. April 2018 - December 2018
    # to handle such cases, month is lazily evaluated
    for month_div in bs4_tree.find_all("div", class_="profile-timeline"):
        record_month = None
        month_data = {}
        for record_div in month_div.find_all("div", class_="profile-rollup-wrapper"):
            parsed_record = _parse_timeline_update_record(record_div)
            if not parsed_record:  # ignore empty months
                continue
            for record_repo, record_activity in parsed_record.items():
                if record_repo not in month_data:
                    month_data[record_repo] = {}
                # we might have several activities in the same record repository
                # in a given month, e.g. issues, PRs and commits
                month_data[record_repo].update(record_activity)
            record_month = record_month or pd.to_datetime(
                month_div.h3.text.strip()).strftime('%Y-%m')
        if month_data:
            yield record_month, month_data


def guard(func):
    # TODO: once released in stutils, reuse from there
    semaphore = threading.Lock()

    @wraps(func)
    def wrapper(*args, **kwargs):
        semaphore.acquire()
        try:
            return func(*args, **kwargs)
        finally:
            semaphore.release()

    return wrapper


class Scraper(object):

    _instance = None  # singleton instance
    cookies = None  # cookies for non-API URLs
    # limit is imposed if over 40 requests are made in 80 seconds
    # thus, keeping track of issued requests
    queue = None
    # after many experiments, 40/121 looks to be the fastest option
    queue_max_size = 40
    queue_time_length = 121

    def __new__(cls, *args, **kwargs):  # Singleton
        if not isinstance(cls._instance, cls):
            cls._instance = super(Scraper, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        self.cookies = requests.get(BASE_URL).cookies
        self.queue = six.moves.queue.Queue(maxsize=self.queue_max_size)

    @guard
    def _request(self, url, params=None, headers=None):
        headers = headers or HEADERS

        if not url.startswith(BASE_URL):
            url = BASE_URL + url

        while True:
            if self.queue.full():
                sleep_interval = self.queue.get() - time.time() + self.queue_time_length
                if sleep_interval > 0:
                    logging.info("Hibernating for %.2f seconds to maintain "
                                 "GitHub XHR rate limit..", sleep_interval)
                    time.sleep(sleep_interval)

            self.queue.put(time.time())
            r = requests.get(
                url, cookies=self.cookies, headers=headers, params=params)
            if r.status_code == 429:
                logging.info("Hit GitHub XHR rate limit, retry in 10 seconds..")
                time.sleep(10)
                continue

            break

        r.raise_for_status()
        return r

    def project_contributor_stats(self, repo_slug):
        # type: (str) -> dict
        """Get top 100 contributors weekly commit stats over the project history

        Args:
            repo_slug (str): <owner_login>/<repo_name>

        Returns:
            list: A list of top 100 contributors in the repo, with their logins,
                total number of commits and weekly contribution counts as number
                of lines added, changed or deleted. Note that weeks are
                started on Sunday and represented by a Unix timestamp.

        >>> Scraper().project_contributor_stats('pandas-dev/pandas')
        [{u'author': {u'avatar': u'https://avatars0.githubusercontent.com/u/1435085?s=60&v=4',
           u'hovercard_url': u'/hovercards?user_id=1435085',
           u'id': 1435085,
           u'login': u'blbradley',
           u'path': u'/blbradley'},
          u'total': 8,
          u'weeks': [{u'a': 0, u'c': 0, u'd': 0, u'w': 1249171200},
           {u'a': 0, u'c': 0, u'd': 0, u'w': 1249776000},
           {u'a': 0, u'c': 0, u'd': 0, u'w': 1250380800},
        ...}]
        """
        return self._request("/%s/graphs/contributors-data" % repo_slug).json()

    def user_daily_contrib_num(self, user, year):
        # type: (str, int) -> dict
        """ Get number of daily contributions of a GitHub user in a given year.
        This method represents the white and green grid in the profile page.

        Args:
            user (str): The GitHub login of the user.
            year (int): Year of contributions to get

        Returns:
            dict: A dictionary with keys being %Y-%m-%d formatted dates and
                values being the number of contributions. This method does not
                differentiate different types of contributions, so it is a sum
                of commits, issues, submitted/reviewed pull requests etc.

        >>> Scraper().user_daily_contrib_num('user2589', 2018)
        {'2017-12-31': 0,
         '2018-01-01': 0,
         '2018-01-02': 15,
         ...
         '2018-12-31': 0}
        """
        url = "/users/%s/contributions?from=%d-12-01&to=%d-12-31&full_graph=1" \
              % (user, year, year)
        year = str(year)
        tree = ElementTree.fromstring(self._request(url).text)

        return {rect.attrib['data-date']: int(rect.attrib.get('data-count'))
                for rect in tree.iter('rect')
                if rect.attrib.get('class') == 'day'
                and rect.attrib.get('data-date', '').startswith(year)}

    def links_to_recent_user_activity(self, user):
        """ Get user events as a 2-tuple generator: (date, link).

        Events include: commits, issues and refs creation (tags/branches).
        Internally, this method is using Atom feed.
        The result includes up to couple month of activity;
        sometimes it also misses up to one month of recent events.

        Args:
            user (str): The GitHub login of the user.

        Returns:
            Generator: A generator of two-tuples:
                (<%Y-%m-%d date>, link to the activity)
                It seems like this feed only includes tags and commits

        >>> list(Scraper().links_to_recent_user_activity('user2589'))
        [('2018-12-01', '/user2589/Q/tree/master'),
         ('2018-12-01'),
          '/user2589/Q/commit/9184f20f939a70e3930ef762cc83906220433fc8'),
         ('2018-11-20', '/user2589/TAC_Github/tree/master'),
         ...]
        """
        warnings.warn(
            "This method is know to return incomplete data."
            "Proceed with caution.", DeprecationWarning)

        def extract_links(text):
            tree = ElementTree.fromstring(text)

            date = None
            for span in tree.iter('span'):
                if 'f6' not in span.attrib.get('class', '').split(" "):
                    continue
                try:
                    date = pd.to_datetime(span.text.strip())
                except ValueError:
                    continue
                break

            links = []
            for link in tree.iter('a'):
                href = link.attrib.get('href', '')
                chunks = href.split("/")
                # hrefs start with "/" so chunks[0] is an empty string
                # this is why 'commit/issue/tree' is chunks[3], not [2]
                if len(chunks) < 5 or \
                        chunks[3] not in ('commit', 'issue', 'tree'):
                    continue
                if href not in links:
                    links.append(href)
                    yield (date, href)

        page = None
        while True:
            r = self._request('/%s' % user, params={'page': page},
                              headers={'Accept': 'application/atom+xml'})
            page = 1 if page is None else page + 1

            activity_log = feedparser.parse(r.text).entries
            if not activity_log:
                return

            for record in activity_log:
                for chunk in record['content']:
                    for ts, link in extract_links(chunk['value'].encode('utf8')):
                        yield ts.strftime("%Y-%m-%d"), link

    def full_user_activity_timeline(self, user, start=None, to=None):
        # type: (str, Optional[str], Optional[str]) -> Generator[Tuple[str, Dict]]
        """
        >>> pd.DataFrame(
        ...     stgithub.Scraper().full_user_activity_timeline(
        ...              'user2589', start='2017-01', to='2017-08')
        ... ).set_index(['month', 'repo'])
                                    commits  created_repository  pull_requests
        month   repo
        2019-01 ssc-oscar/oscar.py      1.0                 NaN            NaN
                bagrat/pylease          NaN                 NaN            1.0
                user2589/pylease        NaN                 1.0            NaN
        """
        if start:
            if not isinstance(start, datetime.datetime):
                start = pd.to_datetime(start)
            start = start.strftime('%Y-%m')
        if to:
            if not isinstance(to, datetime.datetime):  # str or unicode
                to = pd.to_datetime(to)
            now = to.strftime('%Y-%m-%d')
        else:
            now = datetime.datetime.now().strftime('%Y-%m-%d')

        url = '/%s?tab=overview&include_header=no&utf8=✓&from=%s&to=%s' % (
            user, now[:8] + '01', now)

        while True:
            soup = BeautifulSoup(self._request(url).text, 'html.parser')
            for month_div in soup.find_all('div', class_='contribution-activity-listing'):
                for month, data in _parse_timeline_update(month_div):
                    if start and month < start:
                        return
                    for repo, activity in data.items():
                        activity['repo'] = repo
                        activity['month'] = month
                        yield activity
            form = soup.form
            if not form:
                break
            url = form.attrs['data-url']
            if not form.button:
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Get a user contribution timeline")
    parser.add_argument('user', type=str,
                        help='GitHub login of the user to parse')
    parser.add_argument('--from', type=str, nargs='?',
                        help='Lower end of the date range, default: no limit')
    parser.add_argument('--to', type=str, nargs='?',
                        help='Upper end of the date range, default: now')
    parser.add_argument('-o', '--output', default="-",
                        type=argparse.FileType('w'),
                        help='Output filename, "-" or skip for stdin')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help="Log progress to stderr")
    args = parser.parse_args()

    logging.basicConfig(format='%(asctime)s %(message)s',
                        level=logging.INFO if args.verbose else logging.WARNING)

    COLUMNS = ('commits', 'issues', 'pull_requests', 'reviews',
               'private_contrib', 'created_repository', 'joined_org')

    df = pd.DataFrame(Scraper().full_user_activity_timeline(args.user)
                      ).set_index(['month', 'repo']).fillna(0).astype(int)
    df.to_csv(args.output)
