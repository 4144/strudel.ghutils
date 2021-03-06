#!/usr/bin/env python

import csv
import json
import os
from typing import Generator
import unittest

from bs4 import BeautifulSoup
import pandas as pd
import six

import stgithub


class TestGitHub(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = 'fixtures'
        self.scraper = stgithub.Scraper()
        self.repo_slug = 'pandas-dev/pandas'
        self.user = 'user2589'

    def _test_datestring(self, ds, month=False):
        # month = False: test for %Y-%m-%d
        # month = True: test for %Y-%m
        self.assertIsInstance(ds, str)
        self.assertEqual(len(ds), 7 if month else 10)
        self.assertTrue(ds[4] == '-')
        self.assertTrue(ds[:4].isdigit() and ds[5:7].isdigit())
        if not month:
            self.assertTrue(ds[7] == '-')
            self.assertTrue(ds[8:].isdigit(),
                            "%s is not a proper date" % ds)
        return True

    def test_normalize_text(self):
        self.assertEqual(
            stgithub.normalize_text("\nHello   world  \t\n!"), 'Hello world !')

    def test_extract_repo(self):
        self.assertEqual(stgithub.extract_repo("/org/repo"), 'org/repo')
        self.assertEqual(
            stgithub.extract_repo("/org/repo/blabla?something=foo"), 'org/repo')

    def test_parse_record(self):
        fixtures_dir = os.path.join(self.fixtures_dir, 'record')
        test_filename = os.path.join(fixtures_dir, 'record_test.csv')

        fh = open(test_filename)
        for fname, result_json in csv.reader(fh):
            expected_result = json.loads(result_json)
            if 'null' in expected_result:
                # fix json serialization of None
                expected_result[None] = expected_result.pop('null')

            with open(os.path.join(fixtures_dir, fname), 'rb') as fh:
                input_text = fh.read()

            tree = BeautifulSoup(input_text, 'html.parser')
            result = stgithub._parse_timeline_update_record(tree)
            self.assertDictEqual(
                expected_result, result,
                "Record parsing result is different from expected for %s:\n"
                "Expected: %s\n"
                "Got: %s" % (fname, expected_result, result)
            )
        fh.close()

    def test_parse_month(self):
        fixtures_dir = os.path.join(self.fixtures_dir, 'month')
        for fname in os.listdir(fixtures_dir):
            if not fname.endswith('.html'):
                continue

            with open(os.path.join(fixtures_dir, fname)) as fh:
                input_text = fh.read()

            tree = BeautifulSoup(input_text, 'html.parser')
            result = stgithub._parse_timeline_update(tree)
            self.assertIsInstance(result, Generator)
            month, chunk = next(result)
            self.assertIsInstance(month, str)
            self.assertIsInstance(chunk, dict)
            self._test_datestring(month, True)

    def test_project_contributor_stats(self):
        stats = self.scraper.project_contributor_stats(self.repo_slug)
        self.assertIsInstance(stats, list)
        self.assertGreaterEqual(len(stats), 0)
        self.assertIsInstance(stats[0], dict)
        self.assertTrue(
            all(field in stats[0] for field in ('author', 'total', 'weeks')))
        self.assertIsInstance(stats[0]['total'], int)

    def test_user_daily_contrib_num(self):
        contribs = self.scraper.user_daily_contrib_num('user2589', 2018)
        self.assertIsInstance(contribs, dict)
        self.assertEqual(len(contribs), 365)
        self.assertTrue(all(self._test_datestring(k) for k in contribs.keys()))
        self.assertTrue(all(isinstance(v, int) for v in contribs.values()))
        self.assertTrue(all(v >= 0 for v in contribs.values()))

    def test_extract_activity_feed_links(self):
        fpath = os.path.join(self.fixtures_dir, 'activity_feed', 'chunk.html')
        fh = open(fpath, 'rb')
        chunk_text = fh.read()
        fh.close()
        gen = stgithub._extract_activity_feed_links(chunk_text)
        self.assertIsInstance(gen, Generator)
        date, href = next(gen)
        self.assertEqual(date, '2019-01-09')
        self.assertEqual(href, '/CMUSTRUDEL/strudel.ghutils/tree/master')

    def test_links_to_recent_user_activity(self):
        gen = self.scraper.links_to_recent_user_activity(self.user)
        self.assertIsInstance(gen, Generator)
        results = list(gen)
        self.assertGreater(len(results), 50)
        first_res = results[0]
        self.assertIsInstance(first_res, tuple)
        self.assertEqual(len(first_res), 2)
        self._test_datestring(first_res[0])
        self.assertIsInstance(first_res[1], six.string_types)

    def test_full_user_activity_timeline(self):
        gen = self.scraper.full_user_activity_timeline(self.user)
        self.assertIsInstance(gen, Generator)
        # postpone test on full timeline until the end

        results = pd.DataFrame(
            self.scraper.full_user_activity_timeline(
                self.user, '2017-06', '2017-06-30')
        ).set_index(['month', 'repo'])
        self.assertSetEqual({'commits', 'issues'}, set(results.columns))
        self.assertSetEqual(
            {'user2589/ghd', 'user2589/csi-project'}, set(results.index.levels[1]))

        results = pd.DataFrame(
            self.scraper.full_user_activity_timeline(
                self.user, '2017-07', '2017-07-31')
        ).set_index(['month', 'repo'])
        self.assertSetEqual(
            {'commits', 'created_repository'}, set(results.columns))
        self.assertSetEqual({'user2589/ghd'}, set(results.index.levels[1]))

        # get the full timeline
        results = list(gen)
        self.assertGreater(len(results), 50)


if __name__ == "__main__":
    unittest.main()
