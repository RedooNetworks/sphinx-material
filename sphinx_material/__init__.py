"""Sphinx Material theme."""

import collections
import os
import sys
from multiprocessing import Manager
from typing import List, Optional, Union
from xml.etree import ElementTree

import docutils.nodes
import jinja2
import slugify
import sphinx.addnodes
import sphinx.builders
import sphinx.environment.adapters.toctree
from sphinx.util import console
import sphinx.util.docutils
import sphinx.writers.html5

from ._version import get_versions

__version__ = get_versions()["version"]
del get_versions

ROOT_SUFFIX = "--page-root"

DEFAULT_THEME_OPTIONS = {
    'features': [],
    'font': {
        'text': 'Roboto',
        'code': 'Roboto Mono'
    },
    'plugins': {
        'search': {},
    },
    'globaltoc_depth': -1,
    'globaltoc_collapse': True,
    'globaltoc_includehidden': True,
}


class CustomHTMLTranslator(sphinx.writers.html5.HTML5Translator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Ensure pygments uses <code> elements, for compatibility with the
        # mkdocs-material CSS which expects that.
        self.highlighter.formatter_args.update(wrapcode=True)

        # Ensure all tables are marked as data tables.  The material CSS only
        # applies to tables with this class, in order to leave tables used for
        # layout purposes alone.
        self.settings.table_style = ','.join(
            self.settings.table_style.split(',') + ['data'])

    def visit_section(self, node: docutils.nodes.section) -> None:
        # Sphinx normally writes sections with a section heading as:
        #
        #     <div id="identifier" class="section"><hN>...</hN>...</div>
        #
        # but that is incompatible with the way scroll-margin-top and the
        # `:target` selector are used in the mkdocs-material CSS.  For
        # compatibility with mkdocs-material, we strip the outer `<div>` and
        # instead add the `id` to the inner `<hN>` element.
        #
        # That is accomplished by overriding `visit_section` and
        # `depart_section` not to add the `<div>` and `</div>` tags, and also
        # modifying `viist_title` to insert the `id`.
        self.section_level += 1

    def depart_section(self, node: docutils.nodes.section) -> None:
        self.section_level -= 1

    def visit_title(self, node: docutils.nodes.title) -> None:
        if isinstance(node.parent, docutils.nodes.section):
            if node.parent.get('ids') and not node.get('ids'):
                node['ids'] = node.parent.get('ids')
                super().visit_title(node)
                del node['ids']
                return
        super().visit_title(node)


def _strip_fragment(url: str) -> str:
    """Returns the url with any fragment identifier removed."""
    fragment_start = url.find('#')
    if fragment_start == -1: return url
    return url[:fragment_start]


class _TocVisitor(docutils.nodes.NodeVisitor):
    """NodeVisitor used by `_get_mkdocs_toc`."""
    def __init__(self,
                 document: docutils.nodes.document,
                 builder: sphinx.builders.Builder,
                 exclude_local: bool = False):
        super().__init__(document)
        self._prev_caption = None
        self._rendered_title = None
        self._url = None
        self._builder = builder
        # Indicates if this node or one of its descendents is the current page.
        self._active = False
        # List of direct children.
        self._children = []
        # If `True`, we are collecting the global rather than local table of
        # contents, and page-local children should be ignored.
        self._exclude_local = exclude_local
        # If `self._exclude_local == True`, Indicates that neither this nodes
        # URL nor any descendent seen so far involves a non-page local link.
        #
        # If `self._exclude_local == False`, this is ignored.
        self._local_only = True

    def _render(self, node: Union[docutils.nodes.Node,
                                  List[docutils.nodes.Node]]):
        """Returns the HTML representation of `node`."""
        if not isinstance(node, list):
            node = [node]
        return ''.join(
            self._builder.render_partial(x)['fragment'] for x in node)

    def _render_title(self, node: Union[docutils.nodes.Node,
                                        List[docutils.nodes.Node]]):
        """Returns the text representation of `node`."""
        if not isinstance(node, list):
            node = [node]
        return ''.join(x.astext() for x in node)

    def visit_reference(self, node: docutils.nodes.reference):
        self._rendered_title = self._render_title(node.children)
        self._url = node.get('refuri')
        if self._exclude_local and self._url.find('#') == -1:
            self._local_only = False
        raise docutils.nodes.SkipChildren

    def visit_compact_paragraph(self, node: docutils.nodes.Element):
        pass

    def visit_toctree(self, node: docutils.nodes.Node):
        raise docutils.nodes.SkipChildren

    def visit_paragraph(self, node: docutils.nodes.Node):
        pass

    def visit_caption(self, node: docutils.nodes.Node):
        self._prev_caption = node
        raise docutils.nodes.SkipChildren

    def visit_bullet_list(self, node: docutils.nodes.bullet_list):
        if self._prev_caption is not None and self._prev_caption.parent is node.parent:
            # Insert as sub-entry of the previous caption.
            title = self._render_title(self._prev_caption.children)
            self._prev_caption = None
            child_visitor = _TocVisitor(self.document,
                                        self._builder,
                                        exclude_local=self._exclude_local)
            if node.get('iscurrent', False):
                child_visitor._active = True
            node.walk(child_visitor)
            url = None
            children = child_visitor._children
            if not child_visitor._local_only:
                self._local_only = False
            if children:
                url = children[0].get('url', None)
            self._children.append({
                'title': title,
                'url': url,
                'children': children,
                'active': child_visitor._active
            })
            raise docutils.nodes.SkipChildren
        # Otherwise, just process the each list_item as direct children.

    def _is_child_local(self, child: dict):
        if not self._url or not child['url']: return False
        return _strip_fragment(self._url) == _strip_fragment(child['url'])

    def get_result(self):
        return {
            'title': self._rendered_title,
            'url': self._url,
            'children': self._children,
            'active': self._active,
        }

    def visit_list_item(self, node: docutils.nodes.list_item):
        # Child node.  Collect its url, title, and any children using a separate
        # `_TocVisitor`.
        child_visitor = _TocVisitor(self.document,
                                    self._builder,
                                    exclude_local=self._exclude_local)
        if node.get('iscurrent', False):
            child_visitor._active = True
        for child in node.children:
            child.walk(child_visitor)
        child_result = child_visitor.get_result()
        if self._exclude_local and child_visitor._local_only:
            pass
        else:
            self._local_only = False
            self._children.append(child_result)
        raise docutils.nodes.SkipChildren


def _get_mkdocs_toc(toc_node: docutils.nodes.Node,
                    builder: sphinx.builders.Builder,
                    exclude_local: bool) -> list:
    """Converts a docutils toc node into a mkdocs-format JSON toc."""
    visitor = _TocVisitor(sphinx.util.docutils.new_document(''),
                          builder,
                          exclude_local=exclude_local)
    toc_node.walk(visitor)
    return visitor._children


class _NavContextObject(list):
    pass


def dict_merge(*dicts: List[collections.Mapping]):
    """Recursively merges the members of one or more dicts."""
    result = dict()
    for d in dicts:
        for k, v in d.items():
            if (isinstance(v, collections.Mapping) and k in result
                    and isinstance(result[k], dict)):
                result[k] = dict_merge(result[k], v)
            else:
                result[k] = v
    return result


def html_page_context(app, pagename, templaetname, context, doctree):
    theme_options = app.config["html_theme_options"]
    theme_options = dict_merge(DEFAULT_THEME_OPTIONS, theme_options)

    # Add global table of contents in mkdocs format.
    global_toc_node = sphinx.environment.adapters.toctree.TocTree(
        app.env).get_toctree_for(
            pagename,
            app.builder,
            collapse=theme_options.get('globaltoc_collapse', False),
            includehidden=theme_options.get('globaltoc_includehidden', True),
            maxdepth=theme_options.get('globaltoc_depth', -1),
        )
    context.update(
        nav=_NavContextObject(_get_mkdocs_toc(global_toc_node, app.builder, exclude_local=True)))
    context['nav'].homepage = dict(
        url=context['pathto'](context['master_doc']),
    )

    # Add local table of contents in mkdocs format.
    local_toc_node = sphinx.environment.adapters.toctree.TocTree(
        app.env).get_toc_for(
            pagename,
            app.builder,
        )
    local_toc = _get_mkdocs_toc(local_toc_node,
                                app.builder,
                                exclude_local=False)
    if len(local_toc) == 1:
        local_toc = local_toc[0]['children']

    num_slashes = pagename.count('/')
    if num_slashes == 0:
        base_url = '.'
    else:
        base_url = '/'.join('..' for _ in range(num_slashes))

    # Add other context values in mkdocs/mkdocs-material format.
    page = dict(
        title=jinja2.Markup.escape(
            jinja2.Markup(context.get('title')).striptags()),
        is_homepage=(pagename == context['master_doc']),
        toc=local_toc,
        meta={
            'hide': [],
            'revision_date': context.get('last_updated')
        },
        content=context.get('body'),
    )
    meta = context.get('meta', {})
    if meta and meta.get('tocdepth') == 0:
        page['meta']['hide'].append('toc')
    if context.get('next'):
        page['next_page'] = {
            'title':
            jinja2.Markup.escape(
                jinja2.Markup(context['next']['title']).striptags()),
            'url':
            context['next']['link'],
        }
    if context.get('prev'):
        page['previous_page'] = {
            'title':
            jinja2.Markup.escape(
                jinja2.Markup(context['prev']['title']).striptags()),
            'url':
            context['prev']['link'],
        }

    version_config = None
    if theme_options.get('version_dropdown'):
        version_config = {
            'provider': 'mike',
            'staticVersions': theme_options.get('version_info'),
            'versionPath': theme_options.get('version_json'),
        }

    context.update(
        config={
            "theme": theme_options,
            'site_url': theme_options.get('site_url'),
            'site_name': context['docstitle'],
            'repo_url': theme_options.get('repo_url', None),
            'repo_name': theme_options.get('repo_name', None),
            "extra": {
                'version': version_config,
                'social': theme_options.get('social'),
                'disqus': theme_options.get('disqus'),
                'manifest': theme_options.get('pwa_manifest'),
            },
            "plugins": theme_options.get('plugins'),
            "google_analytics": theme_options.get("google_analytics"),
        },
        base_url=base_url,
        page=page,
    )


def add_jinja_filters(app):
    # For compatibility with mkdocs
    app.builder.templates.environment.filters['url'] = lambda url: url


def setup(app):
    app.connect("html-page-context", html_page_context)
    app.connect("html-page-context", add_html_link)
    app.connect("builder-inited", add_jinja_filters)
    app.connect("build-finished", create_sitemap)
    app.connect("build-finished", reformat_pages)
    app.connect("build-finished", minify_css)
    app.set_translator('html', CustomHTMLTranslator, override=True)
    manager = Manager()
    site_pages = manager.list()
    sitemap_links = manager.list()
    app.multiprocess_manager = manager
    app.sitemap_links = sitemap_links
    app.site_pages = site_pages
    app.add_html_theme("sphinx_material",
                       os.path.abspath(os.path.dirname(__file__)))
    return {
        "version": __version__,
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }


def add_html_link(app, pagename, templatename, context, doctree):
    """As each page is built, collect page names for the sitemap"""
    base_url = app.config["html_theme_options"].get("site_url", "")
    if base_url:
        if not base_url.endswith('/'):
            base_url += '/'
        full_url = base_url + pagename + app.builder.link_suffix
        app.sitemap_links.append(full_url)
    minify = app.config["html_theme_options"].get("html_minify", False)
    prettify = app.config["html_theme_options"].get("html_prettify", False)
    if minify and prettify:
        raise ValueError("html_minify and html_prettify cannot both be True")
    if minify or prettify:
        app.site_pages.append(os.path.join(app.outdir, pagename + ".html"))


def create_sitemap(app, exception):
    """Generates the sitemap.xml from the collected HTML page links"""
    if (not app.config["html_theme_options"].get("site_url", "")
            or exception is not None or not app.sitemap_links):
        return

    filename = app.outdir + "/sitemap.xml"
    print("Generating sitemap for {0} pages in "
          "{1}".format(len(app.sitemap_links),
                       console.colorize("blue", filename)))

    root = ElementTree.Element("urlset")
    root.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    for link in app.sitemap_links:
        url = ElementTree.SubElement(root, "url")
        ElementTree.SubElement(url, "loc").text = link
    app.sitemap_links[:] = []

    ElementTree.ElementTree(root).write(filename)


def reformat_pages(app, exception):
    if exception is not None or not app.site_pages:
        return
    minify = app.config["html_theme_options"].get("html_minify", False)
    last = -1
    npages = len(app.site_pages)
    transform = "Minifying" if minify else "Prettifying"
    print("{0} {1} files".format(transform, npages))
    transform = transform.lower()
    # TODO: Consider using parallel execution
    for i, page in enumerate(app.site_pages):
        if int(100 * (i / npages)) - last >= 1:
            last = int(100 * (i / npages))
            color_page = console.colorize("blue", page)
            msg = "{0} files... [{1}%] {2}".format(transform, last, color_page)
            sys.stdout.write("\033[K" + msg + "\r")
        with open(page, "r", encoding="utf-8") as content:
            if minify:
                from css_html_js_minify.html_minifier import html_minify

                html = html_minify(content.read())
            else:
                soup = BeautifulSoup(content.read(), features="lxml")
                html = soup.prettify()
        with open(page, "w", encoding="utf-8") as content:
            content.write(html)
    app.site_pages[:] = []
    print()


def minify_css(app, exception):
    if exception is not None or not app.config["html_theme_options"].get(
            "css_minify", False):
        app.multiprocess_manager.shutdown()
        return
    import glob
    from css_html_js_minify.css_minifier import css_minify

    css_files = glob.glob(os.path.join(app.outdir, "**", "*.css"),
                          recursive=True)
    print("Minifying {0} css files".format(len(css_files)))
    for css_file in css_files:
        colorized = console.colorize("blue", css_file)
        msg = "minifying css file {0}".format(colorized)
        sys.stdout.write("\033[K" + msg + "\r")
        with open(css_file, "r", encoding="utf-8") as content:
            css = css_minify(content.read())
        with open(css_file, "w", encoding="utf-8") as content:
            content.write(css)
    print()
    app.multiprocess_manager.shutdown()
