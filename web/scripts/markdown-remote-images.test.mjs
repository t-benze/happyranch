// THR-406 compliance item 2: viewing Markdown content (threads, KB, tasks,
// dreams) must not make the visitor's browser fetch a third-party image URL.
// Renders the real `src/design-system/patterns/Markdown.tsx` with
// react-dom/server and asserts that cross-origin images never become <img>,
// while same-origin/relative images still do. Local evidence only (not wired
// into CI). Run from the repository root:
//   node --test web/scripts/markdown-remote-images.test.mjs
// MARKDOWN_UNDER_TEST=<abs path> renders another copy (base-branch RED proof).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dirname, resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const WEB = resolvePath(dirname(fileURLToPath(import.meta.url)), '..');
const MARKDOWN = process.env.MARKDOWN_UNDER_TEST ?? resolvePath(WEB, 'src/design-system/patterns/Markdown.tsx');

// Mermaid is lazy-imported and never reached by these bodies; keep it out of the bundle.
const stubMermaid = {
  name: 'stub-mermaid',
  setup(b) {
    b.onResolve({ filter: /\/Mermaid$/ }, () => ({ path: 'mermaid-stub', namespace: 'stub' }));
    b.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({ contents: 'export default () => null;', loader: 'js' }));
  },
};

const bundle = await build({
  stdin: {
    contents: `import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server.browser';
import { Markdown } from ${JSON.stringify(MARKDOWN)};
export const render = (props) => renderToStaticMarkup(createElement(Markdown, props));`,
    resolveDir: WEB,
    loader: 'js',
  },
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  alias: { '@': resolvePath(WEB, 'src') },
  nodePaths: [resolvePath(WEB, 'node_modules')],
  plugins: [stubMermaid],
  logLevel: 'silent',
});
const { render } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

// The SPA as served by the daemon: same scheme + host:port.
globalThis.window = { location: { href: 'http://127.0.0.1:8765/orgs/acme/threads/THR-1', protocol: 'http:', host: '127.0.0.1:8765' } };

const imgSrcs = (html) => [...html.matchAll(/<img\b[^>]*>/g)].map((m) => /\bsrc="([^"]*)"/.exec(m[0])?.[1] ?? null);
const anchors = (html) => [...html.matchAll(/<a\b[^>]*>/g)].map((m) => m[0]);

// Every case: authored body -> URL that must never be an <img> source.
const BLOCKED = [
  ['https image on another host', '![tracker](https://evil.example/x.png)', 'https://evil.example/x.png'],
  ['http image on another host', 'look ![](http://evil.example/pixel.gif) here', 'http://evil.example/pixel.gif'],
  ['protocol-relative URL', '![p](//evil.example/x.png)', '//evil.example/x.png'],
  ['same hostname, different port', '![port](http://127.0.0.1:9999/x.png)', 'http://127.0.0.1:9999/x.png'],
  ['same host, different scheme', '![tls](https://127.0.0.1:8765/x.png)', 'https://127.0.0.1:8765/x.png'],
  ['reference-style image', '![ref][r]\n\n[r]: https://evil.example/ref.png', 'https://evil.example/ref.png'],
  ['image with a title', '![t](https://evil.example/t.png "hover")', 'https://evil.example/t.png'],
  ['image in a table cell', '| a |\n| - |\n| ![c](https://evil.example/cell.png) |', 'https://evil.example/cell.png'],
];

for (const [name, body, url] of BLOCKED) {
  test(`blocked: ${name} renders no <img>, only an inert placeholder link`, () => {
    for (const threadMentions of [false, true]) {
      const html = render({ body, threadMentions });
      assert.deepEqual(imgSrcs(html), [], html);
      assert.match(html, /data-remote-image="blocked"/, html);
      const link = anchors(html).find((a) => a.includes(`href="${url}"`));
      assert.ok(link, `placeholder links ${url}: ${html}`);
      assert.match(link, /rel="noopener noreferrer"/);
    }
  });
}

test('blocked: placeholder shows the label, the alt text and the URL', () => {
  const html = render({ body: '![Build status](https://evil.example/badge.svg)' });
  assert.match(html, /Remote image not loaded/);
  assert.match(html, />Build status</);
  assert.match(html, />https:\/\/evil\.example\/badge\.svg<\/a>/);
});

test('blocked: callers can localize the placeholder label', () => {
  const html = render({ body: '![x](https://evil.example/x.png)', remoteImageLabel: '未加载远程图片' });
  assert.match(html, /未加载远程图片/);
  assert.doesNotMatch(html, /Remote image not loaded/);
});

test('blocked: image inside a link (badge) keeps one link and nests no <a>', () => {
  const html = render({ body: '[![CI](https://img.example/ci.svg)](https://ci.example/run/1)' });
  assert.deepEqual(imgSrcs(html), []);
  const all = anchors(html);
  assert.equal(all.length, 1, html);
  assert.match(all[0], /href="https:\/\/ci\.example\/run\/1"/);
  assert.match(html, /data-remote-image="blocked"/);
  assert.match(html, />https:\/\/img\.example\/ci\.svg</);
});

test('blocked: raw HTML <img> in authored Markdown is not rendered as an element', () => {
  const html = render({ body: 'x <img src="https://evil.example/raw.png"> y\n\n<img src="https://evil.example/block.png">' });
  assert.deepEqual(imgSrcs(html), [], html);
});

test('allowed: relative and same-origin images still render as <img>', () => {
  const cases = [
    ['![a](/api/v1/files/a.png)', '/api/v1/files/a.png'],
    ['![b](./b.png)', './b.png'],
    ['![c](c.png)', 'c.png'],
    ['![d](http://127.0.0.1:8765/d.png)', 'http://127.0.0.1:8765/d.png'],
  ];
  for (const [body, src] of cases) {
    const html = render({ body });
    assert.deepEqual(imgSrcs(html), [src], html);
    assert.doesNotMatch(html, /data-remote-image/);
  }
});

test('unchanged: ordinary links keep their href and gain no placeholder', () => {
  const html = render({ body: 'see [docs](https://docs.example/page) now' });
  assert.deepEqual(anchors(html), ['<a href="https://docs.example/page">']);
  assert.doesNotMatch(html, /data-remote-image/);
});
