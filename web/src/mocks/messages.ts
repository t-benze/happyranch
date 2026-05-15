/**
 * Mock messages per thread for the prototype harness.
 *
 * Each thread has 3-8 messages with a mix of `kind: 'message'` (founder
 * and worker speakers), `kind: 'system'` events (invited, extended,
 * archive_requested, archived, abandoned), and at least one
 * `kind: 'decline'` to exercise that bubble variant in `MessageBubble`.
 *
 * Timestamps are stable, in ascending order within each thread.
 */
import type { ThreadMessage } from '@/lib/api/types';

export const MOCK_PARTICIPANTS: Record<string, string[]> = {
  'THR-001': ['founder', 'engineering_head', 'ops_lead'],
  'THR-002': ['founder', 'ops_lead'],
  'THR-003': ['founder', 'support_lead', 'engineering_head'],
  'THR-004': ['founder', 'support_lead'],
  'THR-005': ['founder', 'ops_lead'],
};

export const MOCK_MESSAGES: Record<string, ThreadMessage[]> = {
  'THR-001': [
    {
      seq: 1,
      speaker: 'founder',
      kind: 'message',
      body_markdown:
        'Let’s start a Q4 sweep on Macau pavilion availability. Need three short-list candidates by Friday.',
      addressed_to: ['@all'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-12T09:14:00Z',
    },
    {
      seq: 2,
      speaker: 'engineering_head',
      kind: 'message',
      body_markdown:
        'On it — pulling the latest occupancy numbers from the partner feed. Ops can cross-check transit access.',
      addressed_to: ['founder', 'ops_lead'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-12T09:31:00Z',
    },
    {
      seq: 3,
      speaker: 'ops_lead',
      kind: 'message',
      body_markdown:
        'Cotai venues look strong for evening events; Taipa needs more lead-time on shuttle routing.',
      addressed_to: ['@all'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-12T11:08:00Z',
    },
    {
      seq: 4,
      speaker: 'engineering_head',
      kind: 'message',
      body_markdown:
        'Short-list draft attached. Estoril, Galaxy Convention Wing, Venetian B2 — happy to drill into any.',
      addressed_to: ['founder'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-13T17:42:00Z',
    },
  ],
  'THR-002': [
    {
      seq: 1,
      speaker: 'founder',
      kind: 'message',
      body_markdown:
        'TurboJet pushed a new sailing schedule. Please verify our VIP itineraries still align.',
      addressed_to: ['ops_lead'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-13T16:02:00Z',
    },
    {
      seq: 2,
      speaker: 'ops_lead',
      kind: 'message',
      body_markdown:
        'Checked all four upcoming itineraries. The 09:00 sailings on weekdays are now 09:15 — every Tuesday party shifts by 15 min.',
      addressed_to: ['founder'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-13T17:11:00Z',
    },
    {
      seq: 3,
      speaker: 'founder',
      kind: 'message',
      body_markdown: 'Update the printable PDFs and notify each booked party. Use the standard rewording.',
      addressed_to: ['ops_lead'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-13T17:14:00Z',
    },
    {
      seq: 4,
      speaker: 'ops_lead',
      kind: 'system',
      body_markdown: null,
      addressed_to: null,
      decline_reason: null,
      system_payload: { event: 'extended', new_cap: 600 },
      created_at: '2026-05-14T10:00:00Z',
    },
  ],
  'THR-003': [
    {
      seq: 1,
      speaker: 'founder',
      kind: 'message',
      body_markdown:
        'Need a draft VIP itinerary covering both Lan Kwai Fong and Sheung Wan for the May 25 visitor party. Late-night dining required.',
      addressed_to: ['@all'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-14T08:30:00Z',
    },
    {
      seq: 2,
      speaker: 'support_lead',
      kind: 'message',
      body_markdown:
        'Drafting around the Soho stretch — Yardbird → Aberdeen Street Social → late dessert. Sheung Wan can be Ho Lee Fook + dragon dance route.',
      addressed_to: ['founder'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-14T09:20:00Z',
    },
    {
      seq: 3,
      speaker: 'engineering_head',
      kind: 'decline',
      body_markdown: null,
      addressed_to: ['founder'],
      decline_reason:
        'Out of scope for engineering team — routing this to support_lead who owns visitor itineraries.',
      system_payload: null,
      created_at: '2026-05-14T09:25:00Z',
    },
    {
      seq: 4,
      speaker: 'support_lead',
      kind: 'message',
      body_markdown:
        'Confirmed reservations at all four venues. Sending the printable PDF tomorrow morning.',
      addressed_to: ['founder'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-14T18:02:00Z',
    },
    {
      seq: 5,
      speaker: 'founder',
      kind: 'message',
      body_markdown:
        'Looks great. Add a fallback rain plan for the dragon-dance leg before sending the PDF.',
      addressed_to: ['support_lead'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-14T18:30:00Z',
    },
  ],
  'THR-004': [
    {
      seq: 1,
      speaker: 'founder',
      kind: 'message',
      body_markdown:
        'Group A is asking for a refund on the cancelled Macau leg. Policy says no, but they’re flagging weather. Please review.',
      addressed_to: ['support_lead'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-04T11:45:00Z',
    },
    {
      seq: 2,
      speaker: 'support_lead',
      kind: 'message',
      body_markdown:
        'Weather cancellation triggered the operator clause — we can honor a 70% refund without precedent risk.',
      addressed_to: ['founder'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-04T13:08:00Z',
    },
    {
      seq: 3,
      speaker: 'support_lead',
      kind: 'system',
      body_markdown: null,
      addressed_to: null,
      decline_reason: null,
      system_payload: { event: 'invited', agent: 'ops_lead' },
      created_at: '2026-05-05T08:55:00Z',
    },
    {
      seq: 4,
      speaker: 'founder',
      kind: 'message',
      body_markdown:
        'Approved at 70%. Please draft the customer reply and the KB precedent entry.',
      addressed_to: ['support_lead'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-05T09:30:00Z',
    },
    {
      seq: 5,
      speaker: 'support_lead',
      kind: 'system',
      body_markdown: null,
      addressed_to: null,
      decline_reason: null,
      system_payload: { event: 'archived' },
      created_at: '2026-05-09T18:22:00Z',
    },
  ],
  'THR-005': [
    {
      seq: 1,
      speaker: 'founder',
      kind: 'message',
      body_markdown:
        'Quick pricing check: should we raise the late-summer SAR package by 8% to absorb the new ferry surcharge?',
      addressed_to: ['ops_lead'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-04-30T14:00:00Z',
    },
    {
      seq: 2,
      speaker: 'ops_lead',
      kind: 'message',
      body_markdown:
        'Not in time for the current booking window. Better to keep prices flat and absorb this quarter, revisit in Q3.',
      addressed_to: ['founder'],
      decline_reason: null,
      system_payload: null,
      created_at: '2026-04-30T15:45:00Z',
    },
    {
      seq: 3,
      speaker: 'founder',
      kind: 'system',
      body_markdown: null,
      addressed_to: null,
      decline_reason: null,
      system_payload: {
        event: 'abandoned',
        reason: 'pricing decision deferred to Q3 planning cycle',
      },
      created_at: '2026-05-01T09:00:00Z',
    },
  ],
};
