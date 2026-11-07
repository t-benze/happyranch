/*
 * MenuFocus — keeps focus available around Nextra's mobile menu.
 *
 * Rendered once from theme.config's main. CSS handles initial/no-JS visibility;
 * this component exclusively owns saved-prior inert on the closed mobile aside,
 * from the first closed layout commit. Headless alone owns its modal siblings.
 * Capture and interim Menu focus (or blur) precede inert acquisition. The shared
 * identity slot in ManualSearch owns every deferred close/select/breakpoint move.
 * Reopen, new navigation, error, user focus/pointer input and cleanup cancel it.
 * Escape lets a handled search Escape win. Breakpoints restore an available
 * counterpart, observing Headless release when necessary instead of guessing
 * its timing. No destination move overrides the user's available focus.
 */
import { useRouter } from 'next/router'
import { useMenu } from 'nextra-theme-docs'
import { useEffect, useLayoutEffect, useRef } from 'react'
import {
  claimFocus,
  dropFocus,
  focusSelectedResult,
  focusSlot,
  ownsFocus,
  scheduleFocus
} from './ManualSearch'

const MOBILE = '(max-width: 767px)'
const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect
const sidebar = () => document.querySelector('aside.nextra-sidebar-container')
const menuButton = () => document.querySelector('button.nextra-hamburger')

function isAvailable(element) {
  if (!element?.isConnected || element.closest('[inert]')) return false
  return element.checkVisibility
    ? element.checkVisibility({ visibilityProperty: true })
    : element.getClientRects().length > 0
}

function counterpartCandidates(element, mobile) {
  const href = element.getAttribute('href')
  const candidates = href
    ? sidebar()?.querySelectorAll(`a[href="${CSS.escape(href)}"]`) || []
    : element.matches('input[type=search]')
      ? document.querySelectorAll('input[type=search]')
      : []
  return [...candidates, mobile ? menuButton() : document.getElementById('hr-content')].filter(Boolean)
}

function sameRoute(a, b) {
  if (!a || !b) return false
  const first = new URL(a, location.href)
  const second = new URL(b, location.href)
  return first.pathname === second.pathname && first.search === second.search &&
    first.hash === second.hash
}

export function MenuFocus() {
  const { menu, setMenu } = useMenu()
  const { events } = useRouter()
  const openedAt = useRef(null)
  const menuState = useRef(menu)
  const owned = useRef(null)

  // Restore only our write, on the actual held element (including a detached
  // predecessor). Never touch Headless's scroller, footer, navbar or layout div.
  function syncExclusion(release = false) {
    const aside = sidebar()
    const want = !release && aside && matchMedia(MOBILE).matches && !menuState.current
    const held = owned.current
    if (held && (held.element !== aside || !want)) {
      if (held.element.inert === true) held.element.inert = held.prior
      owned.current = null
    }
    if (want && !owned.current) {
      owned.current = { element: aside, prior: aside.inert }
      aside.inert = true
    }
  }

  function canMove(id) {
    if (!ownsFocus(id) || !matchMedia(MOBILE).matches || menuState.current) return false
    const slot = focusSlot()
    const active = document.activeElement
    return !active || active === document.body || active === slot.interim
  }

  function interim(slot, active) {
    const button = menuButton()
    if (isAvailable(button)) {
      // Set before focusin so our interim does not invalidate its own owner.
      slot.interim = button
      button.focus({ preventScroll: true })
    } else {
      // A visible search modal can make Menu inert. Blur closes that combobox
      // and lets Headless release its own writes; never clear those writes here.
      active?.blur()
    }
  }

  useEffect(() => {
    if (!menu) return
    function down(event) {
      if (event.key !== 'Escape' || event.defaultPrevented || !matchMedia(MOBILE).matches) return
      event.preventDefault()
      setMenu(false)
    }
    window.addEventListener('keydown', down)
    return () => window.removeEventListener('keydown', down)
  }, [menu, setMenu])

  useIsoLayoutEffect(() => {
    menuState.current = menu
    if (menu) {
      dropFocus()
      openedAt.current = location.href
      syncExclusion()
      return
    }
    const opened = openedAt.current
    openedAt.current = null
    if (opened !== null && matchMedia(MOBILE).matches) {
      // Capture before inert, which can otherwise make activeElement BODY.
      const active = document.activeElement
      const slot = focusSlot()
      if (slot?.kind === 'select') {
        if (!active || active === document.body || sidebar()?.contains(active)) interim(slot, active)
      } else if (sidebar()?.contains(active)) {
        const id = claimFocus('close', { opened, phase: 'queued' })
        interim(focusSlot(), active)
        scheduleFocus(id, 2, () => {
          if (canMove(id) && location.href !== opened) {
            focusSelectedResult(location.href, focusSlot().interim)
          }
          dropFocus(id)
        })
      } else {
        dropFocus()
      }
    }
    syncExclusion()
  }, [menu])

  // Also detects a replaced aside on any MenuFocus commit, without re-claiming
  // focus or cancelling a selection just because an unrelated render occurred.
  useIsoLayoutEffect(() => { syncExclusion() })
  useIsoLayoutEffect(() => () => {
    dropFocus()
    syncExclusion(true)
  }, [])

  useEffect(() => {
    const start = as => {
      const slot = focusSlot()
      if (slot?.kind === 'close' && slot.phase === 'queued' && !slot.navAs) {
        slot.cancel?.()
        slot.cancel = null
        slot.navAs = as
        slot.phase = 'nav-pending'
      } else if (!(slot?.kind === 'select' && sameRoute(slot.route, as))) {
        dropFocus()
      }
    }
    const error = (_err, as) => {
      const slot = focusSlot()
      if (slot && sameRoute(slot.navAs || slot.route, as)) dropFocus(slot.id)
    }
    const complete = as => {
      const slot = focusSlot()
      if (slot?.kind !== 'close' || slot.phase !== 'nav-pending' || slot.navAs !== as) return
      const { id } = slot
      slot.phase = 'complete-queued'
      scheduleFocus(id, 2, () => {
        if (canMove(id)) focusSelectedResult(location.href, focusSlot().interim)
        dropFocus(id)
      })
    }
    events.on('routeChangeStart', start)
    events.on('hashChangeStart', start)
    events.on('routeChangeComplete', complete)
    events.on('hashChangeComplete', complete)
    events.on('routeChangeError', error)
    return () => {
      events.off('routeChangeStart', start)
      events.off('hashChangeStart', start)
      events.off('routeChangeComplete', complete)
      events.off('hashChangeComplete', complete)
      events.off('routeChangeError', error)
    }
  }, [events])

  useEffect(() => {
    let last = document.activeElement
    const focusin = event => {
      last = event.target
      const slot = focusSlot()
      if (!slot || event.target === slot.interim) return
      // Headless can refocus a selecting input before mobile close, or on the
      // desktop navbar. That focus belongs to the selection, not a new owner.
      if (event.target === slot.input && (!matchMedia(MOBILE).matches || menuState.current)) return
      dropFocus()
    }
    const pointerdown = event => { if (event.isTrusted) dropFocus() }
    const query = matchMedia(MOBILE)
    const change = () => {
      syncExclusion()
      const id = claimFocus('breakpoint')
      const previous = last
      const select = () => {
        if (!ownsFocus(id)) return true
        const active = document.activeElement
        if (!previous || isAvailable(previous) ||
          (active && active !== document.body && active !== previous)) {
          dropFocus(id)
          return true
        }
        const target = counterpartCandidates(previous, query.matches).find(isAvailable)
        if (!target) return false
        target.focus({ preventScroll: true })
        dropFocus(id)
        return true
      }
      scheduleFocus(id, 2, () => {
        if (select()) return
        // Headless's disappear observer can release after these frames. One
        // observed ancestor inert release gets one re-check, bounded to 1s.
        const observer = new MutationObserver(records => {
          if (!records.some(record => record.attributeName === 'inert' &&
            !record.target.hasAttribute('inert') &&
            counterpartCandidates(previous, query.matches).some(candidate =>
              record.target.contains(candidate)))) return
          observer.disconnect()
          clearTimeout(timer)
          select()
          dropFocus(id)
        })
        const timer = setTimeout(() => dropFocus(id), 1000)
        observer.observe(document, {
          subtree: true,
          attributes: true,
          attributeFilter: ['inert', 'aria-hidden', 'aria-expanded']
        })
        if (ownsFocus(id)) focusSlot().cancel = () => {
          observer.disconnect()
          clearTimeout(timer)
        }
      })
    }
    document.addEventListener('focusin', focusin, true)
    document.addEventListener('pointerdown', pointerdown, true)
    query.addEventListener('change', change)
    return () => {
      document.removeEventListener('focusin', focusin, true)
      document.removeEventListener('pointerdown', pointerdown, true)
      query.removeEventListener('change', change)
    }
  }, [])

  return null
}
