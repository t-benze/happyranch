/*
 * ManualSearch — the manual's navbar/sidebar search box.
 *
 * Adapted from nextra-theme-docs 3.3.1 (src/components/search.tsx,
 * src/components/flexsearch.tsx, src/components/highlight-matches.tsx and the
 * render helpers in src/utils/render.tsx), plugged in through the supported
 * `search.component` theme option.
 *
 * MIT License
 *
 * Copyright (c) 2020 Shu Ding
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 *
 * Index format, matching options, ranking, highlighting, markup, `_`-prefixed
 * theme classes, Combobox/keyboard behavior and mobile-menu handling are kept
 * as upstream. Changes from upstream (search readiness):
 *   1. When the index finishes loading, the CURRENT query is evaluated, so a
 *      query typed while it loaded shows its results without more input (a
 *      cleared query shows nothing). Upstream left a false "No results found."
 *      until the next keystroke.
 *   2. A failed load is not cached: the error is shown, and the next focus (or
 *      typing again after the failure) retries the load. Upstream cached the
 *      rejected promise until a page reload.
 *   3. Searching is a no-op while the index is absent. Upstream threw an
 *      uncaught TypeError on every keystroke after a failed load.
 * `clsx` and `escape-string-regexp` are replaced by the small local helpers
 * below so no undeclared package is imported.
 */
import {
  Combobox,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions
} from '@headlessui/react'
import NextLink from 'next/link'
import { useRouter as useNextRouter } from 'next/router'
import { useMounted, useRouter } from 'nextra/hooks'
import { InformationCircleIcon, SpinnerIcon } from 'nextra/icons'
import { useMenu, useThemeConfig } from 'nextra-theme-docs'
import {
  Fragment,
  memo,
  useCallback,
  useEffect,
  useRef,
  useState
} from 'react'

const DEFAULT_LOCALE = 'en-US'
const INPUTS = new Set(['input', 'select', 'button', 'textarea'])

// Joins the truthy class strings (the subset of clsx used here).
const cn = (...classes) => classes.filter(Boolean).join(' ')

// Same escaping as escape-string-regexp 5.0.0.
const escapeRegExp = string =>
  string.replace(/[|\\{}()[\]^$+*?.]/g, '\\$&').replace(/-/g, '\\x2d')

function renderComponent(ComponentOrNode, props) {
  if (!ComponentOrNode) return null
  if (typeof ComponentOrNode !== 'function') return ComponentOrNode
  return <ComponentOrNode {...props} />
}

function renderString(stringOrFunction, props = {}) {
  const result =
    typeof stringOrFunction === 'function'
      ? stringOrFunction(props)
      : stringOrFunction
  return result || ''
}

const HighlightMatches = memo(function HighlightMatches({ value, match }) {
  if (!value) {
    return null
  }
  const splitText = value.split('')
  const escapedSearch = escapeRegExp(match.trim())
  const regexp = new RegExp(escapedSearch.replaceAll(/\s+/g, '|'), 'ig')
  let result
  let index = 0
  const content = []
  while ((result = regexp.exec(value))) {
    if (result.index === regexp.lastIndex) {
      regexp.lastIndex++
    } else {
      const before = splitText.splice(0, result.index - index).join('')
      const after = splitText
        .splice(0, regexp.lastIndex - result.index)
        .join('')
      content.push(
        before,
        <span key={result.index} className="_text-primary-600">
          {after}
        </span>
      )
      index = regexp.lastIndex
    }
  }
  return (
    <>
      {content}
      {splitText.join('')}
    </>
  )
})

function Search({
  className,
  value,
  onChange,
  onActive,
  loading,
  error,
  results
}) {
  const themeConfig = useThemeConfig()
  const { setMenu } = useMenu()
  const router = useNextRouter()
  const [focused, setFocused] = useState(false)
  const mounted = useMounted()
  const inputRef = useRef(null)

  useEffect(() => {
    function down(event) {
      const input = inputRef.current
      const activeElement = document.activeElement
      const tagName = activeElement?.tagName.toLowerCase()
      if (
        !input ||
        !tagName ||
        INPUTS.has(tagName) ||
        activeElement?.isContentEditable
      )
        return
      if (
        event.key === '/' ||
        (event.key === 'k' &&
          (event.metaKey || /* for non-Mac */ event.ctrlKey))
      ) {
        event.preventDefault()
        input.focus({ preventScroll: true })
      }
    }
    window.addEventListener('keydown', down)
    return () => {
      window.removeEventListener('keydown', down)
    }
  }, [])

  const icon = mounted && !focused && (
    <kbd
      className={cn(
        '_absolute _my-1.5 _select-none ltr:_right-1.5 rtl:_left-1.5',
        '_h-5 _rounded _bg-white _px-1.5 _font-mono _text-[11px] _font-medium _text-gray-500',
        '_border dark:_border-gray-100/20 dark:_bg-black/50',
        'contrast-more:_border-current contrast-more:_text-current contrast-more:dark:_border-current',
        '_items-center _gap-1 _flex',
        'max-sm:_hidden'
      )}
    >
      {navigator.userAgent.includes('Mac') ? (
        <>
          <span className="_text-xs">⌘</span>K
        </>
      ) : (
        'CTRL K'
      )}
    </kbd>
  )

  const handleFocus = useCallback(
    event => {
      const isFocus = event.type === 'focus'
      if (isFocus) onActive?.()
      setFocused(isFocus)
    },
    [onActive]
  )

  const handleChange = useCallback(
    event => {
      const { value } = event.currentTarget
      onChange(value)
    },
    [onChange]
  )

  const handleSelect = useCallback(
    async searchResult => {
      if (!searchResult) return
      inputRef.current?.blur()
      await router.push(searchResult.route)
      setMenu(false)
      onChange('')
    },
    [router, setMenu, onChange]
  )

  return (
    <Combobox onChange={handleSelect}>
      <div
        className={cn(
          '_not-prose', // for blog
          '_relative _flex _items-center',
          '_text-gray-900 dark:_text-gray-300',
          'contrast-more:_text-gray-800 contrast-more:dark:_text-gray-300',
          className
        )}
      >
        <ComboboxInput
          ref={inputRef}
          spellCheck={false}
          className={({ focus }) =>
            cn(
              '_rounded-lg _px-3 _py-2 _transition-colors',
              '_w-full md:_w-64',
              '_text-base _leading-tight md:_text-sm',
              focus
                ? '_bg-transparent nextra-focusable'
                : '_bg-black/[.05] dark:_bg-gray-50/10',
              'placeholder:_text-gray-500 dark:placeholder:_text-gray-400',
              'contrast-more:_border contrast-more:_border-current',
              '[&::-webkit-search-cancel-button]:_appearance-none'
            )
          }
          autoComplete="off"
          type="search"
          onChange={handleChange}
          onFocus={handleFocus}
          onBlur={handleFocus}
          value={value}
          placeholder={renderString(themeConfig.search.placeholder)}
        />
        {icon}
      </div>
      <ComboboxOptions
        transition
        anchor={{ to: 'top end', gap: 10, padding: 16 }}
        className={({ open }) =>
          cn(
            'nextra-search-results', // for userspace styling
            'nextra-scrollbar max-md:_h-full',
            '_border _border-gray-200 _text-gray-100 dark:_border-neutral-800',
            '_z-20 _rounded-xl _py-2.5 _shadow-xl',
            'contrast-more:_border contrast-more:_border-gray-900 contrast-more:dark:_border-gray-50',
            '_backdrop-blur-lg _bg-[rgb(var(--nextra-bg),.8)]',
            'motion-reduce:_transition-none _transition-opacity',
            open ? '_opacity-100' : '_opacity-0',
            error || loading || !results.length
              ? 'md:_h-[100px]'
              : // headlessui adds max-height as style, use !important to override
                'md:!_max-h-[min(calc(100vh-5rem),400px)]',
            '_w-full md:_w-[576px]',
            'empty:_invisible'
          )
        }
      >
        {error ? (
          <span className="_flex _select-none _justify-center _gap-2 _p-8 _text-center _text-sm _text-red-500">
            <InformationCircleIcon className="_size-5" />
            {renderString(themeConfig.search.error)}
          </span>
        ) : loading ? (
          <span className="_flex _select-none _justify-center _gap-2 _p-8 _text-center _text-sm _text-gray-400">
            <SpinnerIcon className="_size-5 _animate-spin" />
            {renderComponent(themeConfig.search.loading)}
          </span>
        ) : results.length ? (
          results.map(searchResult => (
            <Fragment key={searchResult.id}>
              {searchResult.prefix}
              <ComboboxOption
                as={NextLink}
                value={searchResult}
                href={searchResult.route}
                className={({ focus }) =>
                  cn(
                    '_mx-2.5 _break-words _rounded-md',
                    'contrast-more:_border',
                    focus
                      ? '_text-primary-600 contrast-more:_border-current _bg-primary-500/10'
                      : '_text-gray-800 dark:_text-gray-300 contrast-more:_border-transparent',
                    '_block _scroll-m-12 _px-2.5 _py-2'
                  )
                }
              >
                {searchResult.children}
              </ComboboxOption>
            </Fragment>
          ))
        ) : (
          value && renderComponent(themeConfig.search.emptyResult)
        )}
      </ComboboxOptions>
    </Combobox>
  )
}

const indexes = {}
const loadIndexesPromises = new Map()

const loadIndexes = (basePath, locale) => {
  const key = basePath + '@' + locale
  if (loadIndexesPromises.has(key)) {
    return loadIndexesPromises.get(key)
  }
  const promise = loadIndexesImpl(basePath, locale)
  loadIndexesPromises.set(key, promise)
  // A failed load is not cached, so the next attempt fetches again.
  promise.catch(() => {
    if (loadIndexesPromises.get(key) === promise) {
      loadIndexesPromises.delete(key)
    }
  })
  return promise
}

const loadIndexesImpl = async (basePath, locale) => {
  const [searchData, Document] = await Promise.all([
    fetch(`${basePath}/_next/static/chunks/nextra-data-${locale}.json`).then(
      response => response.json()
    ),
    import('flexsearch').then(mod => mod.default.Document)
  ])

  const pageIndex = new Document({
    cache: 100,
    tokenize: 'full',
    document: {
      id: 'id',
      index: 'content',
      store: ['title']
    },
    context: {
      resolution: 9,
      depth: 2,
      bidirectional: true
    }
  })

  const sectionIndex = new Document({
    cache: 100,
    tokenize: 'full',
    document: {
      id: 'id',
      index: 'content',
      tag: 'pageId',
      store: ['title', 'content', 'url', 'display']
    },
    context: {
      resolution: 9,
      depth: 2,
      bidirectional: true
    }
  })

  let pageId = 0

  for (const [route, structurizedData] of Object.entries(searchData)) {
    let pageContent = ''
    ++pageId

    for (const [key, content] of Object.entries(structurizedData.data)) {
      const [headingId, headingValue] = key.split('#')
      const url = route + (headingId ? '#' + headingId : '')
      const title = headingValue || structurizedData.title
      const paragraphs = content.split('\n')

      sectionIndex.add({
        id: url,
        url,
        title,
        pageId: `page_${pageId}`,
        content: title,
        ...(paragraphs[0] && { display: paragraphs[0] })
      })

      for (let i = 0; i < paragraphs.length; i++) {
        sectionIndex.add({
          id: `${url}_${i}`,
          url,
          title,
          pageId: `page_${pageId}`,
          content: paragraphs[i]
        })
      }

      pageContent += ` ${title} ${content}`
    }

    pageIndex.add({
      id: pageId,
      title: structurizedData.title,
      content: pageContent
    })
  }

  indexes[locale] = [pageIndex, sectionIndex]
}

export function ManualSearch({ className }) {
  const { locale = DEFAULT_LOCALE, basePath } = useRouter()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [results, setResults] = useState([])
  const [search, setSearch] = useState('')
  // The latest typed value, read when the index finishes loading.
  const latestSearch = useRef('')

  const doSearch = search => {
    if (!search) {
      setResults([])
      return
    }
    if (!indexes[locale]) return
    const [pageIndex, sectionIndex] = indexes[locale]

    // Show the results for the top 5 pages
    const pageResults =
      pageIndex.search(search, 5, {
        enrich: true,
        suggest: true
      })[0]?.result || []

    const results = []
    const pageTitleMatches = {}

    for (let i = 0; i < pageResults.length; i++) {
      const result = pageResults[i]
      pageTitleMatches[i] = 0

      // Show the top 5 results for each page
      const sectionResults =
        sectionIndex.search(search, 5, {
          enrich: true,
          suggest: true,
          tag: `page_${result.id}`
        })[0]?.result || []

      let isFirstItemOfPage = true
      const occurred = {}

      for (let j = 0; j < sectionResults.length; j++) {
        const { doc } = sectionResults[j]
        const isMatchingTitle = doc.display !== undefined
        if (isMatchingTitle) {
          pageTitleMatches[i]++
        }
        const { url, title } = doc
        const content = doc.display || doc.content
        if (occurred[url + '@' + content]) continue
        occurred[url + '@' + content] = true
        results.push({
          _page_rk: i,
          _section_rk: j,
          route: url,
          prefix: isFirstItemOfPage && (
            <div
              className={cn(
                '_mx-2.5 _mb-2 [&:not(:first-child)]:_mt-6 _select-none _border-b _border-black/10 _px-2.5 _pb-1.5 _text-xs _font-semibold _uppercase _text-gray-500 dark:_border-white/20 dark:_text-gray-300',
                'contrast-more:_border-gray-600 contrast-more:_text-gray-900 contrast-more:dark:_border-gray-50 contrast-more:dark:_text-gray-50'
              )}
            >
              {result.doc.title}
            </div>
          ),
          children: (
            <>
              <div className="_text-base _font-semibold _leading-5">
                <HighlightMatches match={search} value={title} />
              </div>
              {content && (
                <div className="excerpt _mt-1 _text-sm _leading-[1.35rem] _text-gray-600 dark:_text-gray-400 contrast-more:dark:_text-gray-50">
                  <HighlightMatches match={search} value={content} />
                </div>
              )}
            </>
          )
        })
        isFirstItemOfPage = false
      }
    }

    setResults(
      results
        .sort((a, b) => {
          // Sort by number of matches in the title.
          if (a._page_rk === b._page_rk) {
            return a._section_rk - b._section_rk
          }
          if (pageTitleMatches[a._page_rk] !== pageTitleMatches[b._page_rk]) {
            return pageTitleMatches[b._page_rk] - pageTitleMatches[a._page_rk]
          }
          return a._page_rk - b._page_rk
        })
        .map(res => ({
          id: `${res._page_rk}_${res._section_rk}`,
          route: res.route,
          prefix: res.prefix,
          children: res.children
        }))
    )
  }

  const preload = useCallback(async () => {
    if (indexes[locale]) {
      // Another search box loaded the index after this one failed.
      if (error) {
        setError(false)
        doSearch(latestSearch.current)
      }
      return
    }
    setError(false)
    setLoading(true)
    try {
      await loadIndexes(basePath, locale)
    } catch {
      setError(true)
      setLoading(false)
      return
    }
    setLoading(false)
    doSearch(latestSearch.current)
  }, [locale, basePath, error])

  const handleChange = value => {
    setSearch(value)
    latestSearch.current = value
    if (loading) return
    // Typing again after a failed load retries it.
    if (error) {
      preload()
      return
    }
    doSearch(value)
  }

  return (
    <Search
      loading={loading}
      error={error}
      value={search}
      onChange={handleChange}
      onActive={preload}
      className={className}
      results={results}
    />
  )
}
