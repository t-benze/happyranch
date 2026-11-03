/*
 * MenuFocus — keeps keyboard focus visible around Nextra's mobile menu.
 *
 * Below Nextra's md breakpoint the sidebar is a slide-down menu. While it is
 * closed, styles/globals.css hides it (visibility: hidden), so its search box,
 * links and theme control leave the Tab order. Nextra 3.3.1 neither closes the
 * menu on Escape nor moves focus when the menu or the layout hides the
 * focused control. This component, rendered once from the theme's `main`
 * (theme.config.jsx), renders nothing and:
 *   - closes the open mobile menu on Escape (a control that handled Escape
 *     first, like open search results, wins);
 *   - when the menu closes with focus left inside it: after a navigation
 *     (a menu link or anchor) focuses that page's heading the same way a
 *     selected search result does, otherwise focuses the Menu button. This
 *     waits two frames, so a selected search result (ManualSearch.jsx) is
 *     focused first;
 *   - when a breakpoint change hides the focused control, focuses the shown
 *     link with the same href or the shown search box, else the Menu button
 *     (mobile) or the page's h1 (desktop).
 * It acts only when focus is on the page body or on the hidden control, so it
 * never overrides a place the user moved focus to.
 */
import { useRouter } from 'next/router'
import { useMenu } from 'nextra-theme-docs'
import { useEffect, useRef } from 'react'
import { focusSelectedResult } from './ManualSearch'

// Nextra's md breakpoint: below it the sidebar is the mobile menu.
const MOBILE = '(max-width: 767px)'

const sidebar = () => document.querySelector('aside.nextra-sidebar-container')
const menuButton = () => document.querySelector('button.nextra-hamburger')

function isShown(element) {
  if (!element?.isConnected) return false
  return element.checkVisibility
    ? element.checkVisibility({ visibilityProperty: true })
    : element.getClientRects().length > 0
}

// Runs `callback` two frames from now; returns a cancel function.
function afterFrames(callback) {
  let id = requestAnimationFrame(() => {
    id = requestAnimationFrame(callback)
  })
  return () => cancelAnimationFrame(id)
}

// Focus is on the page body or inside the closed (hidden) mobile menu.
function stranded() {
  const active = document.activeElement
  return !active || active === document.body || !!sidebar()?.contains(active)
}

// The shown control that does what the hidden `element` did.
function counterpart(element, mobile) {
  const href = element.getAttribute('href')
  const candidates = href
    ? sidebar()?.querySelectorAll(`a[href="${CSS.escape(href)}"]`) || []
    : element.matches('input[type=search]')
      ? document.querySelectorAll('input[type=search]')
      : []
  return (
    [...candidates].find(isShown) ||
    (mobile ? menuButton() : document.getElementById('hr-content'))
  )
}

export function MenuFocus() {
  const { menu, setMenu } = useMenu()
  const { events } = useRouter()
  // The URL when the menu opened, to tell a navigation close from others.
  const openedAt = useRef(null)
  const navigating = useRef(false)
  // The menu closed with focus inside it and has not moved it yet.
  const leftInMenu = useRef(false)

  // Escape closes the open mobile menu; the close effect below moves focus.
  // It does not also clear the search box: the browser's clearing of a
  // type=search input reopens Headless UI's (empty) results, which keeps the
  // navbar inert while the box has focus.
  useEffect(() => {
    if (!menu) return
    function down(event) {
      if (
        event.key !== 'Escape' ||
        event.defaultPrevented ||
        !matchMedia(MOBILE).matches
      )
        return
      event.preventDefault()
      setMenu(false)
    }
    window.addEventListener('keydown', down)
    return () => window.removeEventListener('keydown', down)
  }, [menu, setMenu])

  // The menu closed with focus left inside it.
  useEffect(() => {
    if (menu) {
      openedAt.current = location.href
      leftInMenu.current = false
      return
    }
    const opened = openedAt.current
    openedAt.current = null
    if (opened === null || !matchMedia(MOBILE).matches) return
    leftInMenu.current = !!sidebar()?.contains(document.activeElement)
    return afterFrames(() => {
      // A navigation still loading moves focus when it completes (below).
      if (navigating.current) return
      leftInMenu.current = false
      if (!stranded()) return
      if (location.href !== opened) {
        focusSelectedResult(location.href, document.activeElement)
      } else {
        menuButton()?.focus()
      }
    })
  }, [menu])

  // A navigation from the menu (a link or anchor in it) completed.
  useEffect(() => {
    let cancel = () => {}
    const start = () => {
      navigating.current = true
    }
    const end = () => {
      navigating.current = false
    }
    const complete = () => {
      end()
      cancel()
      if (!leftInMenu.current) return
      leftInMenu.current = false
      cancel = afterFrames(() => {
        if (!matchMedia(MOBILE).matches || !stranded()) return
        focusSelectedResult(location.href, document.activeElement)
      })
    }
    events.on('routeChangeStart', start)
    events.on('hashChangeStart', start)
    events.on('routeChangeComplete', complete)
    events.on('hashChangeComplete', complete)
    events.on('routeChangeError', end)
    return () => {
      cancel()
      events.off('routeChangeStart', start)
      events.off('hashChangeStart', start)
      events.off('routeChangeComplete', complete)
      events.off('hashChangeComplete', complete)
      events.off('routeChangeError', end)
    }
  }, [events])

  // A breakpoint change hid the focused control.
  useEffect(() => {
    let last = null
    const focusin = event => {
      last = event.target
    }
    const query = matchMedia(MOBILE)
    let cancel = () => {}
    const change = () => {
      cancel()
      cancel = afterFrames(() => {
        const active = document.activeElement
        if (!last || isShown(last)) return
        if (active && active !== document.body && active !== last) return
        counterpart(last, query.matches)?.focus()
      })
    }
    document.addEventListener('focusin', focusin)
    query.addEventListener('change', change)
    return () => {
      cancel()
      document.removeEventListener('focusin', focusin)
      query.removeEventListener('change', change)
    }
  }, [])

  return null
}
