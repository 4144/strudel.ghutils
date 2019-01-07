# Interface to unofficial GitHub API

- get user contributions timeline
- user contribution stats 
    (crude but fast version of the contributions timeline)
- get project weekly contributors stats


### Installation

    pip install --user --upgrade strudel.ghutils

### Reference

Basic usage:

    >>> from stgithub import Scraper
    >>> scraper = Scraper()
    >>> scraper.user_daily_contrib_num('user2589')
    

Please see <https://cmustrudel.github.io/strudel.ghutils> for full reference.