from . import six
from .six import text_type
import time
import warnings
from .util import parse_timestamp
from .listing import List, PageProperty, PagePropertyGenerator, RevisionsIterator, GeneratorList
from .errors import InsufficientPermission, LoginError, UserBlocked, ProtectedPageError, NoWriteApi, EditError, APIError


class Page(object):

    def __init__(self, site, name, info=None, extra_properties=None):
        if type(name) is type(self):
            return self.__dict__.update(name.__dict__)
        self.site = site
        self.name = name

        if not info:
            if extra_properties:
                prop = 'info|' + '|'.join(six.iterkeys(extra_properties))
                extra_props = []
                [extra_props.extend(extra_prop) for extra_prop in six.itervalues(extra_properties)]
            else:
                prop = 'info'
                extra_props = ()

            if type(name) is int:
                info = self.site.api('query', prop=prop, pageids=name,
                                     inprop='protection', *extra_props)
            else:
                info = self.site.api('query', prop=prop, titles=name,
                                     inprop='protection', *extra_props)
            info = six.next(six.itervalues(info['query']['pages']))
        self._info = info

        self.namespace = info.get('ns', 0)
        self.name = info.get('title', u'')
        if self.namespace:
            self.page_title = self.strip_namespace(self.name)
        else:
            self.page_title = self.name

        self.touched = parse_timestamp(info.get('touched'))
        self.revision = info.get('lastrevid', 0)
        self.exists = 'missing' not in info
        self.length = info.get('length')
        self.protection = dict([(i['type'], (i['level'], i['expiry'])) for i in info.get('protection', ()) if i])
        self.redirect = 'redirect' in info
        self.pageid = info.get('pageid', None)
        self.contentmodel = info.get('contentmodel', None)
        self.pagelanguage = info.get('pagelanguage', None)
        self.restrictiontypes = info.get('restrictiontypes', None)

        self.last_rev_time = None
        self.edit_time = None

    def redirects_to(self):
        """ Returns the redirect target page, or None if the page is not a redirect page."""
        info = self.site.api('query', prop='pageprops', titles=self.name, redirects='')['query']
        if 'redirects' in info:
            for page in info['redirects']:
                if page['from'] == self.name:
                    return Page(self.site, page['to'])
            return None
        else:
            return None

    def resolve_redirect(self):
        """ Returns the redirect target page, or the current page if it's not a redirect page."""
        target_page = self.redirects_to()
        if target_page is None:
            return self
        else:
            return target_page

    def __repr__(self):
        return "<Page object '%s' for %s>" % (self.name.encode('utf-8'), self.site)

    def __unicode__(self):
        return self.name

    @staticmethod
    def strip_namespace(title):
        if title[0] == ':':
            title = title[1:]
        return title[title.find(':') + 1:]

    @staticmethod
    def normalize_title(title):
        # TODO: Make site dependent
        title = title.strip()
        if title[0] == ':':
            title = title[1:]
        title = title[0].upper() + title[1:]
        title = title.replace(' ', '_')
        return title

    def can(self, action):
        """
        Check if the current user has the right to carry out some action with the current page.

            >>> page.can('edit')
            True

        """
        level = self.protection.get(action, (action, ))[0]
        if level == 'sysop':
            level = 'editprotected'

        return level in self.site.rights

    def get_token(self, type, force=False):
        return self.site.get_token(type, force, title=self.name)

    def get_expanded(self):
        """Deprecated. Use page.text(expandtemplates=True) instead"""
        warnings.warn("page.get_expanded() was deprecated in mwclient 0.7.0 and will be removed in 0.8.0, use page.text(expandtemplates=True) instead.",
                      category=DeprecationWarning, stacklevel=2)

        return self.text(expandtemplates=True)

    def edit(self, *args, **kwargs):
        """Deprecated. Use page.text() instead"""
        warnings.warn("page.edit() was deprecated in mwclient 0.7.0 and will be removed in 0.8.0, please use page.text() instead.",
                      category=DeprecationWarning, stacklevel=2)
        return self.text(*args, **kwargs)

    def text(self, section=None, expandtemplates=False):
        """
        Get the current wikitext of the page, or of a specific section.
        If the page does not exist, an empty string is returned.

        Args:
            section (int): numbered section or `None` to get the whole page (default: `None`)
            expandtemplates (bool): set to `True` to expand templates (default: `False`)
        """

        if not self.can('read'):
            raise InsufficientPermission(self)
        if not self.exists:
            return u''
        if section is not None:
            section = text_type(section)

        revs = self.revisions(prop='content|timestamp', limit=1, section=section, expandtemplates=expandtemplates)
        try:
            rev = revs.next()
            text = rev['*']
            self.last_rev_time = rev['timestamp']
        except StopIteration:
            text = u''
            self.last_rev_time = None
        if not expandtemplates:
            self.edit_time = time.gmtime()
        return text

    def save(self, text, summary=u'', minor=False, bot=True, section=None, **kwargs):
        """
        Update the text of a section or the whole page by performing an edit operation.
        """
        if not self.site.logged_in and self.site.force_login:
            # Should we really check for this?
            raise LoginError(self.site, 'By default, mwclient protects you from ' +
                             'accidentally editing without being logged in. If you ' +
                             'actually want to edit without logging in, you can set ' +
                             'force_login on the Site object to False.')
        if self.site.blocked:
            raise UserBlocked(self.site.blocked)
        if not self.can('edit'):
            raise ProtectedPageError(self)

        if not self.site.writeapi:
            raise NoWriteApi(self)

        data = {}
        if minor:
            data['minor'] = '1'
        if not minor:
            data['notminor'] = '1'
        if self.last_rev_time:
            data['basetimestamp'] = time.strftime('%Y%m%d%H%M%S', self.last_rev_time)
        if self.edit_time:
            data['starttimestamp'] = time.strftime('%Y%m%d%H%M%S', self.edit_time)
        if bot:
            data['bot'] = '1'
        if section:
            data['section'] = section

        data.update(kwargs)

        def do_edit():
            result = self.site.api('edit', title=self.name, text=text,
                                   summary=summary, token=self.get_token('edit'),
                                   **data)
            if result['edit'].get('result').lower() == 'failure':
                raise EditError(self, result['edit'])
            return result
        try:
            result = do_edit()
        except APIError as e:
            if e.code == 'badtoken':
                # Retry, but only once to avoid an infinite loop
                self.get_token('edit', force=True)
                try:
                    result = do_edit()
                except APIError as e:
                    self.handle_edit_error(e, summary)
            else:
                self.handle_edit_error(e, summary)

        # 'newtimestamp' is not included if no change was made
        if 'newtimestamp' in result['edit'].keys():
            self.last_rev_time = parse_timestamp(result['edit'].get('newtimestamp'))
        return result['edit']

    def handle_edit_error(self, e, summary):
        if e.code == 'editconflict':
            raise EditError(self, summary, e.info)
        elif e.code in ('protectedtitle', 'cantcreate', 'cantcreate-anon', 'noimageredirect-anon',
                        'noimageredirect', 'noedit-anon', 'noedit'):
            raise ProtectedPageError(self, e.code, e.info)
        else:
            raise

    def move(self, new_title, reason='', move_talk=True, no_redirect=False):
        """Move (rename) page to new_title.

        If user account is an administrator, specify no_redirect as True to not
        leave a redirect.

        If user does not have permission to move page, an InsufficientPermission
        exception is raised.

        """
        if not self.can('move'):
            raise InsufficientPermission(self)

        if not self.site.writeapi:
            raise NoWriteApi(self)

        data = {}
        if move_talk:
            data['movetalk'] = '1'
        if no_redirect:
            data['noredirect'] = '1'
        result = self.site.api('move', ('from', self.name), to=new_title,
                               token=self.get_token('move'), reason=reason, **data)
        return result['move']

    def delete(self, reason='', watch=False, unwatch=False, oldimage=False):
        """Delete page.

        If user does not have permission to delete page, an InsufficientPermission
        exception is raised.

        """
        if not self.can('delete'):
            raise InsufficientPermission(self)

        if not self.site.writeapi:
            raise NoWriteApi(self)

        data = {}
        if watch:
            data['watch'] = '1'
        if unwatch:
            data['unwatch'] = '1'
        if oldimage:
            data['oldimage'] = oldimage
        result = self.site.api('delete', title=self.name,
                               token=self.get_token('delete'),
                               reason=reason, **data)
        return result['delete']

    def purge(self):
        """Purge server-side cache of page. This will re-render templates and other
        dynamic content.

        """
        self.site.raw_index('purge', title=self.name)

    # def watch: requires 1.14

    # Properties
    def backlinks(self, namespace=None, filterredir='all', redirect=False, limit=None, generator=True):
        """
        List pages that link to the current page, similar to Special:Whatlinkshere.

        API doc: https://www.mediawiki.org/wiki/API:Backlinks

        """
        prefix = List.get_prefix('bl', generator)
        kwargs = dict(List.generate_kwargs(prefix, namespace=namespace, filterredir=filterredir))
        if redirect:
            kwargs['%sredirect' % prefix] = '1'
        kwargs[prefix + 'title'] = self.name

        return List.get_list(generator)(self.site, 'backlinks', 'bl', limit=limit, return_values='title', **kwargs)

    def categories(self, generator=True):
        """
        List categories used on the current page.

        API doc: https://www.mediawiki.org/wiki/API:Categories

        """
        if generator:
            return PagePropertyGenerator(self, 'categories', 'cl')
        else:
            # TODO: return sortkey if wanted
            return PageProperty(self, 'categories', 'cl', return_values='title')

    def embeddedin(self, namespace=None, filterredir='all', limit=None, generator=True):
        """
        List pages that transclude the current page.

        API doc: https://www.mediawiki.org/wiki/API:Embeddedin

        Args:
            namespace (int): Restricts search to a given namespace (Default: None)
            filterredir (str): How to filter redirects, either 'all' (default),
                'redirects' or 'nonredirects'.
            limit (int): Maximum amount of pages to return per request
            generator (bool): Use generator

        Returns:
            mwclient.listings.list.List: Page iterator
        """
        prefix = List.get_prefix('ei', generator)
        kwargs = dict(List.generate_kwargs(prefix, namespace=namespace, filterredir=filterredir))
        kwargs[prefix + 'title'] = self.name

        return List.get_list(generator)(self.site, 'embeddedin', 'ei', limit=limit, return_values='title', **kwargs)

    def extlinks(self):
        """
        List external links from the current page.

        API doc: https://www.mediawiki.org/wiki/API:Extlinks

        """
        return PageProperty(self, 'extlinks', 'el', return_values='*')

    def images(self, generator=True):
        """
        List files/images embedded in the current page.

        API doc: https://www.mediawiki.org/wiki/API:Images

        """
        if generator:
            return PagePropertyGenerator(self, 'images', '')
        else:
            return PageProperty(self, 'images', '', return_values='title')

    def iwlinks(self):
        """
        List interwiki links from the current page.

        API doc: https://www.mediawiki.org/wiki/API:Iwlinks

        """
        return PageProperty(self, 'iwlinks', 'iw', return_values=('prefix', '*'))

    def langlinks(self, **kwargs):
        """
        List interlanguage links from the current page.

        API doc: https://www.mediawiki.org/wiki/API:Langlinks

        """
        return PageProperty(self, 'langlinks', 'll', return_values=('lang', '*'), **kwargs)

    def links(self, namespace=None, generator=True, redirects=False):
        """
        List links to other pages from the current page.

        API doc: https://www.mediawiki.org/wiki/API:Links

        """
        prefix = List.get_prefix('pl', generator)
        kwargs = dict(List.generate_kwargs(prefix, namespace=namespace))

        if redirects:
            kwargs['redirects'] = '1'
        if generator:
            return PagePropertyGenerator(self, 'links', 'pl', **kwargs)
        else:
            return PageProperty(self, 'links', 'pl', return_values='title', **kwargs)

    def revisions(self, startid=None, endid=None, start=None, end=None,
                  dir='older', user=None, excludeuser=None, limit=50,
                  prop='ids|timestamp|flags|comment|user', expandtemplates=False, section=None):
        """
        List revisions of the current page.

        API doc: https://www.mediawiki.org/wiki/API:Revisions

        Args:
            startid (int): Revision ID to start listing from.
            endid (int): Revision ID to stop listing at.
            start (str): Timestamp to start listing from.
            end (str): Timestamp to end listing at.
            dir (str): Direction to list in: 'older' (default) or 'newer'.
            user (str): Only list revisions made by this user.
            excludeuser (str): Exclude revisions made by this user.
            limit (int): The maximum number of revisions to return per request.
            prop (str): Which properties to get for each revision,
                default: 'ids|timestamp|flags|comment|user'
            expandtemplates (bool): Expand templates in rvprop=content output
            section (int): If rvprop=content is set, only retrieve the contents of this section.

        Returns:
            mwclient.listings.list.List: Revision iterator
        """
        kwargs = dict(List.generate_kwargs('rv', startid=startid, endid=endid, start=start,
                                           end=end, user=user, excludeuser=excludeuser))
        kwargs['rvdir'] = dir
        kwargs['rvprop'] = prop
        if expandtemplates:
            kwargs['rvexpandtemplates'] = '1'
        if section is not None:
            kwargs['rvsection'] = section

        return RevisionsIterator(self, 'revisions', 'rv', limit=limit, **kwargs)

    def templates(self, namespace=None, generator=True):
        """
        List templates used on the current page.

        API doc: https://www.mediawiki.org/wiki/API:Templates

        """
        prefix = List.get_prefix('tl', generator)
        kwargs = dict(List.generate_kwargs(prefix, namespace=namespace))
        if generator:
            return PagePropertyGenerator(self, 'templates', prefix, **kwargs)
        else:
            return PageProperty(self, 'templates', prefix, return_values='title', **kwargs)
