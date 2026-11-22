"""The docs site is the published form of ``docs/`` plus the public API reference.

Issue #82: mkdocs + mkdocstrings, ``make docs`` is the local build, the gate
fails on a broken cross-reference. Issue #138 (Ty, 2026-09-11): nothing hosts the
site. GitLab renders ``docs/`` on ``main``, ``make docs-release`` commits the build
under ``docs/site/`` on a release branch, and the getting-started guide (where the
README overhaul of 2026-10-11 moved it) records the rendered station page as the URL
that belongs on a station label. The theme (Material for MkDocs) offers a light and a
dark palette, and because that build is opened as ``file://`` with no network, no page
names a font, stylesheet, or script on another host. One fetch is accepted and is not
covered here: the theme's bundle loads the Mermaid renderer from a CDN when a page holds
a diagram, and with no network that diagram stays as its source (CHANGELOG, 1.0.1).
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import hilt

ROOT = Path(__file__).resolve().parents[1]
MKDOCS = ROOT / "mkdocs.yml"
API_PAGE = ROOT / "docs" / "api.md"
README = ROOT / "README.md"
GUIDE = ROOT / "docs" / "getting-started.md"
MAKEFILE = ROOT / "Makefile"
CI = ROOT / ".gitlab-ci.yml"

REPO_URL = "https://gitlab.com/Tyler-Meador/HILT"
DOCS_URL = f"{REPO_URL}/-/tree/main/docs"
STATION_URL = f"{REPO_URL}/-/blob/main/docs/floor-technician.md"

_IDENT = re.compile(r"^::: hilt\.([A-Za-z_][A-Za-z0-9_]*)$", re.MULTILINE)
_NAV_ENTRY = re.compile(r":\s*([A-Za-z0-9_./-]+\.md)\s*$", re.MULTILINE)
# What a page makes the browser fetch before it can be read: scripts, and the links that
# load a stylesheet or open a connection. An ordinary ``<a href>`` is not one of them.
_REMOTE_SCRIPT = re.compile(r'<script\b[^>]*\bsrc="(https?://[^"]+)"')
_REMOTE_LINK = re.compile(
    r'<link\b[^>]*\brel="(?:stylesheet|preconnect|preload)"[^>]*\bhref="(https?://[^"]+)"'
)


def _recipe(target: str, makefile: str) -> str:
    """Body of a Makefile target, from the header to the next unindented rule."""
    lines = makefile.splitlines()
    body: list[str] = []
    collecting = False
    for line in lines:
        if collecting:
            if line.startswith("\t"):
                body.append(line)
                continue
            if not line.strip() or line.startswith("#") or line.startswith("."):
                continue
            break
        if re.match(rf"^{re.escape(target)}\s*:", line):
            collecting = True
    return "\n".join(body)


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("site")
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "-d", str(dest)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return dest


class TestDocsSiteConfig:
    def test_mkdocs_yml_exists_and_is_strict(self) -> None:
        text = MKDOCS.read_text(encoding="utf-8")
        assert "strict: true" in text
        assert "mkdocstrings:" in text
        assert f"site_url: {DOCS_URL}/" in text
        # A release branch commits the build under docs/site/ and a station opens it
        # from a clone: links must end in .html, and the committed build must not be
        # swept into the next one.
        assert "use_directory_urls: false" in text
        assert "site/" in text.split("exclude_docs:", 1)[1]

    def test_theme_offers_light_and_dark_with_a_toggle(self) -> None:
        # Three palette entries: follow the system, light, dark. Each carries a toggle,
        # so the reader can leave the system setting and get back to it.
        theme = (
            MKDOCS.read_text(encoding="utf-8").split("\ntheme:", 1)[1].split("\nextra_css:", 1)[0]
        )
        assert "name: material" in theme
        assert 'media: "(prefers-color-scheme)"' in theme
        assert "scheme: default" in theme
        assert "scheme: slate" in theme
        assert theme.count("toggle:") == 3
        # A web font is a request to another host on every page.
        assert "font: false" in theme

    def test_every_markdown_page_is_in_the_nav(self) -> None:
        # Recursive: docs/hil/ groups the HIL interface design notes under one
        # directory rather than flattening every note into docs/*.md (issue #91).
        nav_pages = set(_NAV_ENTRY.findall(MKDOCS.read_text(encoding="utf-8")))
        docs_dir = ROOT / "docs"
        # docs/site/ is a committed build on a release branch, never a source page.
        on_disk = {
            path.relative_to(docs_dir).as_posix()
            for path in docs_dir.rglob("*.md")
            if path.relative_to(docs_dir).parts[0] != "site"
        }
        assert on_disk, "docs/ grew no markdown pages"
        missing = on_disk - nav_pages
        extra = nav_pages - on_disk
        assert missing == set()
        assert extra == set()

    def test_hil_nav_labels_carry_no_status(self) -> None:
        """Status lives on the HIL index table, not in the mkdocs label.

        GPIO, PWM, and Serial/UART already read ``<Name> (design note)``. Putting
        ``draft`` in the label makes signing the note cost a second ``mkdocs.yml``
        edit for no gain (Zavala on !128).
        """
        entries = re.findall(
            r"^\s+- (.+): hil/(?!index\.md)([A-Za-z0-9_./-]+\.md)\s*$",
            MKDOCS.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        assert entries, "HIL nav grew no design-note pages"
        for label, _page in entries:
            assert label.endswith("(design note)"), label
            assert "draft" not in label.lower(), label

    def test_api_page_identifiers_match_dunder_all(self) -> None:
        names = _IDENT.findall(API_PAGE.read_text(encoding="utf-8"))
        assert names == list(hilt.__all__)


class TestReadmeRecordsThePublishedUrl:
    def test_package_urls_point_at_the_tree_and_the_repo(self) -> None:
        # The PyPI page is the first place a developer finds the docs and the tracker
        # (issue #103); a URL that drifts from mkdocs.yml sends them to a dead site,
        # and the only live site is the tree itself (#138).
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        urls = data["project"]["urls"]
        mkdocs = MKDOCS.read_text(encoding="utf-8")
        assert urls["Source"] == REPO_URL
        assert urls["Homepage"] == REPO_URL
        assert urls["Documentation"] == DOCS_URL
        assert f"repo_url: {urls['Source']}" in mkdocs
        assert urls["Issues"] == f"{urls['Source']}/-/issues"
        assert "Typing :: Typed" in data["project"]["classifiers"]
        assert (ROOT / "src" / "hilt" / "py.typed").is_file()

    def test_readme_leads_to_the_getting_started_guide(self) -> None:
        readme = README.read_text(encoding="utf-8")
        assert "(docs/getting-started.md)" in readme
        assert "docs/floor-technician.md" in readme

    def test_the_project_page_gets_no_relative_link(self) -> None:
        # PyPI does not resolve a relative link: the 1.0.0 and 1.0.1 pages showed a
        # broken hero image and dead links into docs/. README.md keeps its relative
        # links for the tree; the build's substitutions must leave none for the page.
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert "readme" in data["project"]["dynamic"]
        hook = data["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
        assert [fragment["path"] for fragment in hook["fragments"]] == ["README.md"]
        page = README.read_text(encoding="utf-8")
        for substitution in hook["substitutions"]:
            replacement = substitution["replacement"].replace("$HFPR_VERSION", "9.9.9")
            page = re.sub(substitution["pattern"], replacement, page)
        targets = re.findall(r"\]\(([^)\s]+)\)", page)
        assert f"{REPO_URL}/-/raw/v9.9.9/hero.png" in targets
        assert f"{REPO_URL}/-/blob/v9.9.9/docs/getting-started.md" in targets
        relative = [t for t in targets if not t.startswith(("https://", "#"))]
        assert relative == []

    def test_getting_started_names_the_docs_tree(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        assert DOCS_URL in guide
        assert "make docs-release" in guide

    def test_getting_started_names_the_station_url(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        assert STATION_URL in guide


class TestGateBuildsTheSite:
    def test_makefile_docs_target_is_in_check(self) -> None:
        makefile = MAKEFILE.read_text(encoding="utf-8")
        assert _recipe("docs", makefile)
        assert "$(MAKE) docs" in _recipe("check", makefile)

    def test_makefile_docs_serve_is_a_preview_and_not_a_gate(self) -> None:
        makefile = MAKEFILE.read_text(encoding="utf-8")
        serve = _recipe("docs-serve", makefile)
        assert "mkdocs serve" in serve
        # A docstring is a page source: saving one under src/ rebuilds the reference.
        assert "--watch src" in serve
        assert "docs-serve" not in _recipe("check", makefile)

    def test_makefile_smoke_target_is_in_check(self) -> None:
        # The smoke is the only place the wheel itself is exercised (issue #102): it
        # must run in the gate, from inside .smoke/ so src/ is off the path, and it
        # must start both console entry points, not just import the package.
        makefile = MAKEFILE.read_text(encoding="utf-8")
        assert "$(MAKE) smoke" in _recipe("check", makefile)
        smoke = _recipe("smoke", makefile)
        assert "uv build" in _recipe("build", makefile)
        assert "cd .smoke && venv/bin/hilt-check --list-rules" in smoke
        assert "cd .smoke && venv/bin/hilt run ../examples/hello_world.py" in smoke

    def test_makefile_docs_release_builds_into_the_tree(self) -> None:
        # The release build lands where a release branch commits it and stays out of
        # the gate, so main never grows a docs/site/ by accident.
        makefile = MAKEFILE.read_text(encoding="utf-8")
        release = _recipe("docs-release", makefile)
        assert "mkdocs build --strict" in release
        assert "mv site docs/site" in release
        assert "docs-release" not in _recipe("check", makefile)

    def test_there_is_no_pipeline_definition(self) -> None:
        # Ty, 2026-09-10 on #104: no CI, permanently. The 2026-09-11 rulings on #138
        # left one dormant scheduled soak; Ty removed the file outright on 2026-10-11
        # (0a9a8dc), so nothing on the server can start a pipeline and the bench is
        # the only machine that runs the gate or the soak.
        assert not CI.exists()


class TestBuiltSite:
    def test_homepage_and_station_page_exist(self, site: Path) -> None:
        assert (site / "index.html").is_file()
        assert (site / "floor-technician.html").is_file()
        # Opened as file:// from a clone, only a link that names the file resolves.
        assert "floor-technician.html" in (site / "index.html").read_text(encoding="utf-8")

    def test_every_public_name_appears_in_the_api_reference(self, site: Path) -> None:
        html = (site / "api.html").read_text(encoding="utf-8")
        missing = [name for name in hilt.__all__ if name not in html]
        assert missing == []

    def test_pages_are_themed_and_name_nothing_on_another_host(self, site: Path) -> None:
        # A station opens the committed build from a clone with no network (#138): a
        # font, a stylesheet, or a script on a CDN is a page that renders late or wrong.
        # This reads the HTML only. The Mermaid renderer the theme's bundle fetches at
        # run time is the one accepted exception (module docstring), and it is not here.
        pages = sorted(site.rglob("*.html"))
        assert pages
        for page in pages:
            html = page.read_text(encoding="utf-8")
            assert "data-md-color-scheme" in html, page.name
            assert _REMOTE_SCRIPT.findall(html) == [], page.name
            assert _REMOTE_LINK.findall(html) == [], page.name
        assert (site / "assets" / "stylesheets" / "hilt.css").is_file()

    def test_search_works_from_a_file_url(self, site: Path) -> None:
        # A file:// page can neither fetch the index nor start a Web Worker. The offline
        # plugin inlines the index as a script, and the worker shim is a file in the
        # build rather than the CDN copy the plugin would otherwise name.
        assert (site / "search" / "search_index.js").is_file()
        assert (site / "assets" / "javascripts" / "iframe-worker-shim.js").is_file()
        home = (site / "index.html").read_text(encoding="utf-8")
        assert 'src="assets/javascripts/iframe-worker-shim.js"' in home

    def test_mermaid_fences_become_diagram_blocks(self, site: Path) -> None:
        # Drawn by a renderer fetched at run time; offline, the block shows its source.
        html = (site / "lifecycle.html").read_text(encoding="utf-8")
        assert html.count('<pre class="mermaid">') == 3

    def test_published_schemas_are_copied(self, site: Path) -> None:
        assert (site / "schema" / "result-v1.schema.json").is_file()
        assert (site / "schema" / "timeline-v1.schema.json").is_file()
